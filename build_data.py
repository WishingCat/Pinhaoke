#!/usr/bin/env python3
"""
Parse PKU course schedule Excel files and generate courses.json.
Uses raw XML parsing (no openpyxl dependency needed).
"""

import zipfile
import xml.etree.ElementTree as ET
import json
import re
import os

NS = {
    'main': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main',
    'r': 'http://schemas.openxmlformats.org/officeDocument/2006/relationships',
}
REL_NS = 'http://schemas.openxmlformats.org/package/2006/relationships'

COLUMNS = ['课程名', '教师', '开课单位', '上课时间及教室', '备注']


def read_shared_strings(z):
    """Read shared strings table from xlsx zip."""
    try:
        ss_xml = z.read('xl/sharedStrings.xml')
    except KeyError:
        return []
    root = ET.fromstring(ss_xml)
    strings = []
    for si in root.findall(f'{{{NS["main"]}}}si'):
        texts = si.findall(f'.//{{{NS["main"]}}}t')
        strings.append(''.join(t.text or '' for t in texts))
    return strings


def get_sheet_info(z):
    """Get sheet names and their file paths."""
    wb_xml = z.read('xl/workbook.xml')
    root = ET.fromstring(wb_xml)
    sheets_elem = root.find(f'{{{NS["main"]}}}sheets')

    rels_xml = z.read('xl/_rels/workbook.xml.rels')
    rels_root = ET.fromstring(rels_xml)
    rid_map = {}
    for rel in rels_root.findall(f'{{{REL_NS}}}Relationship'):
        rid_map[rel.get('Id')] = rel.get('Target')

    sheets = []
    for s in sheets_elem.findall(f'{{{NS["main"]}}}sheet'):
        name = s.get('name')
        rid = s.get(f'{{{NS["r"]}}}id')
        target = rid_map.get(rid, '')
        if not target.startswith('/'):
            target = 'xl/' + target
        else:
            target = target[1:]
        sheets.append((name, target))
    return sheets


def col_letter_to_index(col_str):
    """Convert column letter(s) to 0-based index. A=0, B=1, ..., Z=25, AA=26."""
    result = 0
    for c in col_str:
        result = result * 26 + (ord(c) - ord('A') + 1)
    return result - 1


def parse_cell_ref(ref):
    """Extract column index from cell reference like 'A1', 'B2', 'AA10'."""
    col_str = ''.join(c for c in ref if c.isalpha())
    return col_letter_to_index(col_str)


def parse_sheet(z, sheet_path, shared_strings):
    """Parse a sheet and return list of row data (list of lists)."""
    try:
        sheet_xml = z.read(sheet_path)
    except KeyError:
        return []
    root = ET.fromstring(sheet_xml)
    rows_data = []

    for row_elem in root.findall(f'.//{{{NS["main"]}}}row'):
        cells = row_elem.findall(f'{{{NS["main"]}}}c')
        row_dict = {}
        for cell in cells:
            ref = cell.get('r', '')
            col_idx = parse_cell_ref(ref)
            cell_type = cell.get('t', '')
            val_elem = cell.find(f'{{{NS["main"]}}}v')
            val = val_elem.text if val_elem is not None else ''

            if cell_type == 's' and val:
                val = shared_strings[int(val)]
            elif cell_type == 'inlineStr':
                is_elem = cell.find(f'{{{NS["main"]}}}is')
                if is_elem is not None:
                    texts = is_elem.findall(f'.//{{{NS["main"]}}}t')
                    val = ''.join(t.text or '' for t in texts)

            row_dict[col_idx] = val
        rows_data.append(row_dict)

    return rows_data


def split_time_and_classroom(time_classroom_str):
    """
    Split '上课时间及教室' into separate time and classroom parts.

    Examples:
        '1~15周 每周周一10~12节 理教108' -> ('1~15周 每周周一10~12节', '理教108')
        '1~15周 双周周四7~8节 二教405\n1~15周 每周周二1~2节 二教405'
            -> ('1~15周 双周周四7~8节; 1~15周 每周周二1~2节', '二教405')
    """
    if not time_classroom_str or not time_classroom_str.strip():
        return '', ''

    lines = time_classroom_str.strip().split('\n')
    times = []
    classrooms = set()

    # Pattern: after "X~Y节" there may be a space followed by the classroom
    pattern = re.compile(r'^(.*?(?:\d+~\d+节|\d+节))\s+(.+)$')

    for line in lines:
        line = line.strip()
        if not line:
            continue
        m = pattern.match(line)
        if m:
            times.append(m.group(1).strip())
            classroom = m.group(2).strip()
            if classroom:
                classrooms.add(classroom)
        else:
            # No classroom found in this line, keep the whole line as time
            times.append(line)

    time_str = '\n'.join(times)
    classroom_str = '; '.join(sorted(classrooms)) if classrooms else ''
    return time_str, classroom_str


def parse_xlsx(filepath, student_type):
    """Parse an xlsx file and return a list of course dicts."""
    courses = []
    with zipfile.ZipFile(filepath, 'r') as z:
        shared_strings = read_shared_strings(z)
        sheets = get_sheet_info(z)

        for sheet_name, sheet_path in sheets:
            rows = parse_sheet(z, sheet_path, shared_strings)
            if not rows:
                continue

            # Skip header row (row 0)
            for row_dict in rows[1:]:
                # Extract the 5 columns (indices 0-4)
                raw = [row_dict.get(i, '') for i in range(5)]

                course_name = (raw[0] or '').strip()
                if not course_name:
                    continue

                teacher = (raw[1] or '').strip()
                department = (raw[2] or '').strip()
                time_classroom = (raw[3] or '').strip()
                notes = (raw[4] or '').strip()

                time_str, classroom_str = split_time_and_classroom(time_classroom)

                # If no classroom found in the main field, check notes for common patterns
                if not classroom_str and notes:
                    # Look for classroom-like patterns in notes
                    classroom_patterns = [
                        r'上课地点[：:]?\s*(.+?)(?:[，,。;；]|$)',
                        r'地点[：:]?\s*(.+?)(?:[，,。;；]|$)',
                    ]
                    for cp in classroom_patterns:
                        m = re.search(cp, notes)
                        if m:
                            classroom_str = m.group(1).strip()
                            break
                    # Also check for standalone classroom refs like "二教423" or "理教108"
                    if not classroom_str:
                        m = re.match(
                            r'^[\u4e00-\u9fff]+\d+(?:\s|$|[，,。;；])',
                            notes,
                        )
                        if m:
                            classroom_str = m.group(0).strip().rstrip('，,。;；')

                courses.append({
                    '课程名': course_name,
                    '教师': teacher,
                    '开课单位': department,
                    '上课时间': time_str,
                    '教室': classroom_str,
                    '备注': notes,
                    '课程类别': sheet_name,
                    '学生类别': student_type,
                })

    return courses


def main():
    base_dir = os.path.dirname(os.path.abspath(__file__))

    undergrad_file = os.path.join(
        base_dir, 'pku_undergraduate_course_schedule_spring_2026.xlsx'
    )
    grad_file = os.path.join(
        base_dir, 'pku_graduate_course_schedule_spring_2026.xlsx'
    )

    all_courses = []

    if os.path.exists(undergrad_file):
        print(f'Parsing undergraduate courses: {undergrad_file}')
        courses = parse_xlsx(undergrad_file, '本科生')
        print(f'  Found {len(courses)} courses')
        all_courses.extend(courses)
    else:
        print(f'WARNING: {undergrad_file} not found')

    if os.path.exists(grad_file):
        print(f'Parsing graduate courses: {grad_file}')
        courses = parse_xlsx(grad_file, '研究生')
        print(f'  Found {len(courses)} courses')
        all_courses.extend(courses)
    else:
        print(f'WARNING: {grad_file} not found')

    output_file = os.path.join(base_dir, 'courses.json')
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(all_courses, f, ensure_ascii=False, indent=2)

    # Also generate a JS file for direct <script src> loading (avoids CORS on file://)
    js_file = os.path.join(base_dir, 'courses_data.js')
    with open(js_file, 'w', encoding='utf-8') as f:
        f.write('const COURSES_DATA = ')
        json.dump(all_courses, f, ensure_ascii=False, separators=(',', ':'))
        f.write(';\n')

    print(f'\nTotal courses: {len(all_courses)}')
    print(f'Output written to: {output_file}')
    print(f'JS data written to: {js_file}')

    # Print some stats
    categories = set(c['课程类别'] for c in all_courses)
    departments = set(c['开课单位'] for c in all_courses)
    print(f'Categories: {len(categories)}')
    print(f'Departments: {len(departments)}')

    # Check how many have classrooms
    with_classroom = sum(1 for c in all_courses if c['教室'])
    print(f'Courses with classroom info: {with_classroom}/{len(all_courses)}')


if __name__ == '__main__':
    main()
