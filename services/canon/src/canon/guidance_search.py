"""函釋提到查詢詞時帶出它解釋的條文，與兩臂的結果融合。

條文寫「燃燒」、函釋寫「露天燃燒」：查詢的用語常是函釋的，不是條文的。函釋不是推論的前提
（行政程序法第 159 條），這裡只拿它找條文：被函釋帶出的條文與兩臂的結果以名次倒數融合（k = 60，
同 `retrieval.search_hybrid`），帶出的條文本身仍是現行條文，來源與其他命中相同。

函釋帶出的若是義務條，處罰它的條（連同處罰的處罰，`retrieval.sanction_chain`）緊跟在它之後一併帶出，
與混合檢索命中義務條時同一份展開；這些展開出來的條沒有函釋提到它，件數為 0。
"""

from collections.abc import Sequence

from canon.store.records import SearchHit

#: 詞組的數量與長度（字）。更短的詞幾乎每件函釋都有，更長的不會是使用者打的關鍵字。
MAX_PHRASES = 5
MIN_PHRASE_CHARS = 2
MAX_PHRASE_CHARS = 30

#: 向資料庫要多少條函釋帶出的候選（含展開出來的處罰條），與融合後最多比 `limit` 多回幾條。
CANDIDATES = 15
EXTRA = 5

RRF_K = 60


def guidance_phrases(query: str) -> list[str]:
    """查詢以空白分成的詞組（去重，保留次序）；長度不合的略過。"""
    seen: dict[str, None] = {}
    for word in query.split():
        if MIN_PHRASE_CHARS <= len(word) <= MAX_PHRASE_CHARS:
            seen.setdefault(word)
    return list(seen)[:MAX_PHRASES]


def fuse(
    hits: Sequence[SearchHit], guided: Sequence[tuple[SearchHit, int]], limit: int
) -> list[tuple[SearchHit, int]]:
    """兩臂的結果與函釋帶出的條文，依名次倒數融合；回傳（命中，提到它的函釋件數）。

    同分時兩臂的結果在前。最多回 `limit + EXTRA` 條：函釋帶出的條文不擠掉兩臂的結果。

    函釋帶出的名次只算有函釋提到的條（件數 > 0）；緊跟在來源之後展開出來的處罰條（件數 0）與它的來源
    同名次、同份額：處罰條占一個位置，不把後面的來源往後推，其他條文與兩臂結果的相對次序不因展開而變。
    展開是從來源推導的，不是獨立的證據：處罰條若已在兩臂的結果裡，不加「兩處都有」的分，取兩個位置中
    較前的那個（混合檢索自己的展開也是同一件事：同一條出現兩次取名次最前的，不相加）。
    """
    scored: dict[str, tuple[float, int, int, SearchHit, int]] = {}
    for rank, hit in enumerate(hits, 1):
        scored[hit.iri] = (1 / (RRF_K + rank), 0, rank, hit, 0)
    rank = 1
    for position, (hit, documents) in enumerate(guided):
        if documents > 0 and position > 0:
            rank += 1
        share = 1 / (RRF_K + rank)
        if hit.iri in scored and documents == 0:
            held, side, order, found, shown = scored[hit.iri]
            if share > held:
                scored[hit.iri] = (share, side, min(order, rank), found, shown)
            continue
        if hit.iri in scored:
            score, side, order, found, _ = scored[hit.iri]
            scored[hit.iri] = (score + share, side, order, found, documents)
        else:
            scored[hit.iri] = (share, 1, rank, hit, documents)
    ordered = sorted(scored.values(), key=lambda s: (-s[0], s[1], s[2]))
    return [(hit, documents) for _, _, _, hit, documents in ordered[: limit + EXTRA]]
