"""Local search over verified facts. No network, embeddings, or customer data."""
import argparse
from collections import Counter
import json
import math
from pathlib import Path
import re
import time

ROOT = Path(__file__).resolve().parent
FILES = (ROOT / "knowledge/interview-progress.json", ROOT / "interviews/knowledge.json")


def terms(text):
    result = []
    for word in re.findall(r"[가-힣]+|[a-z0-9][a-z0-9_.-]*", text.lower()):
        result.append(word)
        if re.fullmatch(r"[가-힣]{3,}", word):
            result.extend(word[i:i + 2] for i in range(len(word) - 1))
    return result


def render(fact):
    fields = [("fact", "내용"), ("source", "출처"), ("conditions", "조건"), ("uncertainty", "미확인")]
    return "\n".join(f"{label}: {fact[key]}" for key, label in fields if fact.get(key))


class KnowledgeSearch:
    def __init__(self, facts):
        self.facts = facts
        self.tokens = [Counter(terms(render(f))) for f in facts]
        self.lengths = [sum(t.values()) for t in self.tokens]
        self.average = sum(self.lengths) / max(1, len(facts))
        self.frequency = Counter(t for doc in self.tokens for t in doc)

    def search(self, query, limit=6):
        query_terms = set(terms(query))
        scores = []
        for i, doc in enumerate(self.tokens):
            score = 0.0
            for term in query_terms & doc.keys():
                count = self.frequency[term]
                idf = math.log(1 + (len(self.facts) - count + 0.5) / (count + 0.5))
                frequency = doc[term]
                norm = 1.2 * (0.25 + 0.75 * self.lengths[i] / max(1, self.average))
                score += idf * frequency * 2.2 / (frequency + norm)
            if score:
                scores.append((score, i))
        scores.sort(key=lambda item: (-item[0], item[1]))
        return [{"score": round(score, 3), **self.facts[i]} for score, i in scores[:limit]]


def load_facts(paths=FILES):
    facts = []
    for path in paths:
        data = json.loads(path.read_text())
        items = data if isinstance(data, list) else data.get("facts", data.get("knowledge", []))
        for i, item in enumerate(items):
            if isinstance(item, dict) and isinstance(item.get("fact"), str) and item["fact"].strip():
                # Keep the complete fact, conditions and uncertainty together; a search hit is not a policy decision.
                facts.append({**item, "record": f"{path.relative_to(ROOT)}#{item.get('id', i)}"})
    return facts


def main():
    parser = argparse.ArgumentParser(description="검수된 지식에서 원문 출처·조건·미확인을 함께 검색합니다. 내부 작업용입니다.")
    parser.add_argument("query")
    parser.add_argument("--limit", type=int, default=6)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    if not 1 <= args.limit <= 100:
        parser.error("--limit는 1~100이어야 합니다")
    started = time.perf_counter()
    index = KnowledgeSearch(load_facts())
    indexed = time.perf_counter()
    hits = index.search(args.query, args.limit)
    elapsed = time.perf_counter()
    metadata = {"facts": len(index.facts), "index_ms": round((indexed-started)*1000, 2),
                "search_ms": round((elapsed-indexed)*1000, 2), "results": hits}
    if args.json:
        print(json.dumps(metadata, ensure_ascii=False, indent=2))
    else:
        print(f"지식 {len(index.facts)}건 | 색인 {metadata['index_ms']}ms | 검색 {metadata['search_ms']}ms")
        print("내부 검색 결과입니다. 출처·조건·미확인을 함께 검토하세요. 고객에게 그대로 전달하지 마세요.")
        if not hits:
            print("일치한 지식이 없습니다. 다른 업무 용어로 검색하거나 원문을 확인하세요.")
        for hit in hits:
            print(f"\n[{hit['record']}]\n{render(hit)}")


if __name__ == "__main__":
    main()
