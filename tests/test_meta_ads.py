"""Leitura do espelho Meta sem chamar a API."""
from __future__ import annotations

import unittest

from app.services.meta_ads import _row, _totals


class MetaAdsMirrorTest(unittest.TestCase):
    def test_conversation_and_cost_from_ad_row(self):
        row = _row(
            {
                "campaign_name": "WhatsApp Outubro",
                "adset_name": "Público RH",
                "ad_name": "Criativo balão",
                "impressions": "1000",
                "clicks": "40",
                "spend": "80.00",
                "ctr": "4.0",
                "actions": [
                    {
                        "action_type": "onsite_conversion.messaging_conversation_started_7d",
                        "value": "8",
                    }
                ],
            }
        )
        self.assertEqual(row["ad"], "Criativo balão")
        self.assertEqual(row["conversations"], 8)
        self.assertEqual(row["cost_per_conversation"], 10)
        self.assertEqual(row["spend_label"], "R$ 80,00")
        self.assertEqual(row["cost_label"], "R$ 10,00")

    def test_nao_soma_apelidos_da_mesma_conversa(self):
        row = _row(
            {
                "impressions": "100",
                "clicks": "10",
                "spend": "20",
                "actions": [
                    {
                        "action_type": "onsite_conversion.messaging_conversation_started_7d",
                        "value": "4",
                    },
                    {"action_type": "messaging_conversation_started_7d", "value": "4"},
                    {"action_type": "onsite_conversion.total_messaging_connection", "value": "9"},
                ],
            }
        )
        self.assertEqual(row["conversations"], 4)
        self.assertEqual(row["cost_per_conversation"], 5)

    def test_totals_sum_creatives(self):
        rows = [
            _row({"impressions": "100", "clicks": "10", "spend": "20", "actions": []}),
            _row(
                {
                    "impressions": "50",
                    "clicks": "5",
                    "spend": "10",
                    "actions": [
                        {
                            "action_type": "onsite_conversion.messaging_conversation_started_7d",
                            "value": "2",
                        }
                    ],
                }
            ),
        ]
        totals = _totals(rows)
        self.assertEqual(totals["ads_label"], "2")
        self.assertEqual(totals["conversations_label"], "2")
        self.assertEqual(totals["spend_label"], "R$ 30,00")
        self.assertEqual(totals["cost_label"], "R$ 15,00")


if __name__ == "__main__":
    unittest.main()
