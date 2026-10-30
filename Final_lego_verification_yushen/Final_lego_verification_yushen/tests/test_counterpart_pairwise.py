import unittest

import pandas as pd

from src.relationships import (
    attach_counterpart_groups,
    get_relevant_counterpart,
)


def required_parts(part_nums):
    return pd.DataFrame([
        {
            "part_num": part_num,
            "color_id": position + 1,
            "quantity_required": (position + 1) * 2,
        }
        for position, part_num in enumerate(part_nums)
    ])


def relationships(*rows):
    return pd.DataFrame(
        rows,
        columns=["rel_type", "child_part_num", "parent_part_num"],
    )


class CounterpartPairwiseTests(unittest.TestCase):
    def test_one_sided_relationships_are_not_counterparts(self):
        cases = (
            ("P", ["printed"], "printed", "plain"),
            ("R", ["left"], "right", "left"),
            ("B", ["assembly"], "subpart", "assembly"),
        )

        for rel_type, set_parts, child, parent in cases:
            with self.subTest(rel_type=rel_type):
                relevant = get_relevant_counterpart(
                    required_parts(set_parts),
                    relationships((rel_type, child, parent)),
                )
                self.assertTrue(relevant.empty)

    def test_both_endpoints_are_relevant_for_every_counterpart_type(self):
        relationship_rows = relationships(
            ("P", "printed", "plain"),
            ("R", "right", "left"),
            ("B", "subpart", "assembly"),
        )
        parts = required_parts([
            "printed",
            "plain",
            "right",
            "left",
            "subpart",
            "assembly",
        ])
        parts.loc[
            parts["part_num"].isin(["printed", "plain"]),
            "color_id",
        ] = 85

        relevant = get_relevant_counterpart(parts, relationship_rows)

        self.assertEqual(set(relevant["rel_type"]), {"P", "R", "B"})
        self.assertTrue(relevant["parent_in_set"].all())
        self.assertTrue(relevant["child_in_set"].all())

    def test_subpart_relationship_is_pairwise_not_whole_family(self):
        relationship_rows = relationships(
            ("B", "present-child", "assembly"),
            ("B", "absent-child", "assembly"),
        )
        parts = required_parts(["assembly", "present-child"])

        relevant = get_relevant_counterpart(parts, relationship_rows)

        self.assertEqual(len(relevant), 1)
        self.assertEqual(relevant.iloc[0]["child_part_num"], "present-child")

    def test_one_sided_official_part_stays_a_normal_inventory_row(self):
        parts = required_parts(["printed"])

        attached = attach_counterpart_groups(
            parts,
            relationships(("P", "printed", "plain")),
        )

        self.assertEqual(len(attached), 1)
        self.assertFalse(attached.iloc[0]["has_counterpart"])
        self.assertEqual(attached.iloc[0]["counterpart"], "")
        self.assertEqual(attached.iloc[0]["counterpart_group_id"], "")

    def test_valid_counterparts_do_not_duplicate_rows_or_quantities(self):
        parts = required_parts(["assembly", "first-child", "second-child"])
        original_quantities = parts.set_index("part_num")[
            "quantity_required"
        ].to_dict()

        attached = attach_counterpart_groups(
            parts,
            relationships(
                ("B", "first-child", "assembly"),
                ("B", "second-child", "assembly"),
                ("B", "second-child", "assembly"),
            ),
        )

        self.assertEqual(len(attached), len(parts))
        self.assertEqual(
            attached.set_index("part_num")["quantity_required"].to_dict(),
            original_quantities,
        )
        self.assertTrue(attached["has_counterpart"].all())
        self.assertEqual(attached["part_num"].nunique(), len(parts))

    def test_print_counterparts_match_each_inventory_color(self):
        parts = pd.DataFrame([
            {"part_num": "3069b", "color_id": 19, "quantity_required": 6},
            {"part_num": "3069b", "color_id": 40, "quantity_required": 2},
            {"part_num": "3069b", "color_id": 72, "quantity_required": 6},
            {
                "part_num": "3069bpr0090",
                "color_id": 72,
                "quantity_required": 1,
            },
            {"part_num": "98138", "color_id": 71, "quantity_required": 2},
            {"part_num": "98138", "color_id": 182, "quantity_required": 2},
            {
                "part_num": "98138pr0012",
                "color_id": 179,
                "quantity_required": 3,
            },
        ])
        relationship_rows = relationships(
            ("P", "3069bpr0090", "3069b"),
            ("P", "98138pr0012", "98138"),
        )

        attached = attach_counterpart_groups(parts, relationship_rows)
        keyed = attached.set_index(["part_num", "color_id"])

        self.assertTrue(keyed.loc[("3069b", 72), "has_counterpart"])
        self.assertTrue(
            keyed.loc[("3069bpr0090", 72), "has_counterpart"]
        )
        for key in (
            ("3069b", 19),
            ("3069b", 40),
            ("98138", 71),
            ("98138", 182),
            ("98138pr0012", 179),
        ):
            self.assertFalse(keyed.loc[key, "has_counterpart"])
            self.assertEqual(keyed.loc[key, "counterpart"], "")

        self.assertEqual(len(attached), len(parts))
        self.assertEqual(
            attached["quantity_required"].sum(),
            parts["quantity_required"].sum(),
        )


if __name__ == "__main__":
    unittest.main()
