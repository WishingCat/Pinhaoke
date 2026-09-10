// Loaded only when exporting. No DOM screenshots, external images, or font requests.
const FONT = '"PingFang SC", "Microsoft YaHei", "Noto Sans CJK SC", sans-serif';

export function wrapCanvasText(context, text, width) {
  const lines = [];
  for (const paragraph of String(text || '').split(/\r?\n/)) {
    let line = '';
    for (const character of paragraph) {
      if (line && context.measureText(line + character).width > width) {
        lines.push(line);
        line = '';
      }
      line += character;
    }
    lines.push(line);
  }
  return lines;
}

export function timetableImageScale(width, height) {
  // Keep each edge within iOS canvas limits and pixel storage within 32 MB.
  return Math.min(2, 4096 / width, 4096 / height, Math.sqrt(8_000_000 / (width * height)));
}

export function createTimetableCanvas(snapshot) {
  const canvas = document.createElement('canvas');
  const context = canvas.getContext('2d');
  if (!context) throw new Error('Canvas unavailable');
  const width = 1460, margin = 32, periodWidth = 60, headerHeight = 44;
  const dayWidth = (width - margin * 2 - periodWidth) / 7;
  const cardWidth = dayWidth - 12, textWidth = cardWidth - 20;
  const colors = { ink: '#172B29', muted: '#546762', border: '#D8E4E0', card: '#E5F3EE', accent: '#08766B' };
  const font = (size, weight = 400) => { context.font = `${weight} ${size}px ${FONT}`; };
  const rows = snapshot.rows.map(row => {
    const cells = row.cells.map(cell => {
      const courses = cell.courses.map(course => {
        font(16, 600);
        const name = wrapCanvasText(context, course.name, textWidth);
        font(14);
        const room = wrapCanvasText(context, course.room, textWidth);
        return { name, room, height: 20 + name.length * 23 + room.length * 21 + 4 };
      });
      return { ...cell, courses, height: courses.reduce((sum, course) => sum + course.height + 6, 6) };
    });
    return { ...row, cells, height: Math.max(52, ...cells.map(cell => cell.height)) };
  });
  font(15);
  const notes = snapshot.notes.flatMap(note => wrapCanvasText(context, note, width - margin * 2 - 24));
  const hasConflicts = rows.some(row => row.cells.some(cell => cell.conflict));
  const tableTop = 116;
  const tableBottom = tableTop + headerHeight + rows.reduce((sum, row) => sum + row.height, 0);
  const height = tableBottom + 80 + (hasConflicts ? 34 : 0) + (notes.length ? 48 + notes.length * 24 : 0);
  const scale = timetableImageScale(width, height);
  canvas.width = Math.max(1, Math.floor(width * scale));
  canvas.height = Math.max(1, Math.floor(height * scale));
  context.scale(scale, scale);
  context.textBaseline = 'top';
  context.fillStyle = '#FFFFFF';
  context.fillRect(0, 0, width, height);

  function text(value, x, y, size, color = colors.ink, weight = 400) {
    font(size, weight); context.fillStyle = color; context.fillText(value, x, y);
  }
  function box(x, y, w, h, fill, radius = 0) {
    context.beginPath();
    context.moveTo(x + radius, y);
    context.arcTo(x + w, y, x + w, y + h, radius);
    context.arcTo(x + w, y + h, x, y + h, radius);
    context.arcTo(x, y + h, x, y, radius);
    context.arcTo(x, y, x + w, y, radius);
    context.closePath(); context.fillStyle = fill; context.fill();
  }
  function gridCell(x, y, w, h, fill) {
    context.fillStyle = fill; context.fillRect(x, y, w, h);
    context.strokeStyle = colors.border; context.lineWidth = 1;
    context.strokeRect(x, y, w, h);
  }

  text('我的课表', margin, 30, 30, colors.ink, 700);
  text(snapshot.caption, margin, 74, 18, colors.muted);
  context.textAlign = 'right'; text('拼好课', width - margin, 38, 21, colors.accent, 600);
  context.textAlign = 'left';
  let x = margin;
  snapshot.headings.forEach((label, index) => {
    const cellWidth = index === 0 ? periodWidth : dayWidth;
    gridCell(x, tableTop, cellWidth, headerHeight, '#F0F6F3');
    context.textAlign = 'center'; text(label, x + cellWidth / 2, tableTop + 13, 16, colors.ink, 600);
    context.textAlign = 'left'; x += cellWidth;
  });

  let y = tableTop + headerHeight;
  for (const row of rows) {
    gridCell(margin, y, periodWidth, row.height, '#F7FAF8');
    context.textAlign = 'center'; text(row.label, margin + periodWidth / 2, y + 18, 14, colors.muted);
    context.textAlign = 'left';
    row.cells.forEach((cell, day) => {
      const left = margin + periodWidth + day * dayWidth;
      gridCell(left, y, dayWidth, row.height, cell.conflict ? '#FFF0D6' : '#FFFFFF');
      let top = y + 6;
      for (const course of cell.courses) {
        box(left + 6, top, cardWidth, course.height, colors.card, 8);
        let baseline = top + 10;
        course.name.forEach(line => { text(line, left + 16, baseline, 16, colors.ink, 600); baseline += 23; });
        baseline += 4;
        course.room.forEach(line => { text(line, left + 16, baseline, 14, colors.muted); baseline += 21; });
        top += course.height + 6;
      }
    });
    y += row.height;
  }
  y += 20;
  if (hasConflicts) {
    box(margin, y + 2, 14, 14, '#F4C46B', 3);
    text('浅橙色格子：课程时间可能重叠，请核对周次和选课安排。', margin + 24, y, 15, colors.muted);
    y += 34;
  }
  if (notes.length) {
    text('课程备注与待确认信息', margin, y, 17, colors.ink, 600);
    y += 30;
    notes.forEach(line => { text(line, margin + 12, y, 15, colors.muted); y += 24; });
  }
  text('拼好课 · www.pinhaoke.love', margin, height - 32, 13, colors.muted);
  return canvas;
}
