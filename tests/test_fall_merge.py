from copy import deepcopy
import importlib
import unittest

merge = importlib.import_module("北京大学选课网数据抓取.merge_undergrad_2627_fall")


def course(seq, kind="专业课", **fields):
    return {"课程类型": kind, "课程序号": seq, "数据学期": "26-27第1学期",
            "基本信息": {"课程号": seq, "班号": "1", "教师": "旧教师", "课程类别": "任选", **fields},
            "详细信息": {"中文简介": "旧简介"}}


class FallMergeTests(unittest.TestCase):
    def test_latest_overwrites_blanks_and_teacher_without_moving_old_slots(self):
        baseline = [course("A"), course("B")]
        fresh = course("A", 教师="新教师")
        fresh["详细信息"]["中文简介"] = ""
        merged, audit = merge.merge_rows(baseline, [fresh, course("C")])
        self.assertEqual(merged, [fresh, baseline[1], course("C")])
        self.assertEqual(audit["counts"], {"updated": 1, "retained_old_only": 1, "added": 1})
        self.assertEqual(baseline[0]["基本信息"]["教师"], "旧教师")

    def test_reclassification_reuses_id_and_does_not_keep_obsolete_type(self):
        baseline = [course("A"), course("B")]
        fresh = course("A", "公选课", 课程类别="全校任选")
        merged, audit = merge.merge_rows(baseline, [fresh])
        self.assertEqual(merged, [fresh, baseline[1]])
        self.assertEqual(audit["counts"]["reclassified"], 1)

    def test_other_old_category_keeps_classification_with_new_shared_fields(self):
        baseline = [course("A"), course("A", "劳动教育课", 课程类别="劳动教育")]
        fresh = course("A", 教师="新教师")
        merged, audit = merge.merge_rows(baseline, [fresh])
        self.assertEqual(merged[0], fresh)
        self.assertEqual(merged[1]["课程类型"], "劳动教育课")
        self.assertEqual(merged[1]["基本信息"]["课程类别"], "劳动教育")
        self.assertEqual(merged[1]["基本信息"]["教师"], "新教师")
        self.assertEqual(audit["counts"]["refreshed_other_category"], 1)

    def test_new_cross_category_membership_is_added_without_losing_old_class(self):
        baseline = [course("A", "体育课")]
        fresh = [course("A", "专业课"), course("A", "通识课")]
        merged, _ = merge.merge_rows(baseline, fresh)
        self.assertEqual([r["课程类型"] for r in merged], ["体育课", "专业课", "通识课"])
        self.assertEqual(merged[1:], fresh)

    def test_conflicting_identity_duplicate_or_semester_fail_closed(self):
        baseline = [course("A")]
        bad_identity = course("A", 班号="2")
        bad_term = deepcopy(baseline[0])
        bad_term["数据学期"] = "27-28第1学期"
        for latest in ([bad_identity], [bad_term], [course("A"), course("A")]):
            with self.subTest(latest=latest), self.assertRaises(ValueError):
                merge.merge_rows(baseline, latest)

    def test_ambiguous_reclassification_is_not_guessed(self):
        with self.assertRaisesRegex(ValueError, "Ambiguous"):
            merge.merge_rows([course("A")], [course("A", "通识课"), course("A", "公选课")])


if __name__ == "__main__":
    unittest.main()
