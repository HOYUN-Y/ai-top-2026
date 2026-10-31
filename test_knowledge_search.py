import unittest

from knowledge_search import KnowledgeSearch, load_facts


class KnowledgeSearchTest(unittest.TestCase):
    def test_korean_query_keeps_source_exceptions_and_unknowns(self):
        refund = {"fact": "부분환불 산식은 하자 개체 수에 따른다.", "source": "원문.pdf 제3조",
                  "conditions": "단품 제외, 사진 없이 진술도 가능", "uncertainty": "구매 시점 적용 확인 필요"}
        other = {"fact": "체험 행사는 사전 예약을 받는다.", "source": "행사.pdf"}
        index = KnowledgeSearch([other, refund])
        hits = index.search("부분환불을 계산하려면", 1)
        self.assertEqual(hits[0]["source"], refund["source"])
        self.assertEqual(hits[0]["conditions"], refund["conditions"])
        self.assertEqual(hits[0]["uncertainty"], refund["uncertainty"])
        self.assertEqual(index.search(""), [])
        self.assertEqual(KnowledgeSearch([]).search("환불"), [])


    def test_two_letter_query_matches_longer_korean_word(self):
        index = KnowledgeSearch([{"fact": "환불은 원결제수단으로만 한다."}, {"fact": "체험 예약을 받는다."}])
        self.assertEqual(index.search("환불", 1)[0]["fact"], "환불은 원결제수단으로만 한다.")

    def test_real_files_return_policy_with_record(self):
        hits = KnowledgeSearch(load_facts()).search("비밀번호 OTP", 1)
        self.assertEqual(hits[0]["id"], "privacy-credentials")
        self.assertIn("#privacy-credentials", hits[0]["record"])
        self.assertTrue(hits[0]["conditions"])


if __name__ == "__main__":
    unittest.main()
