from app.services.documents import ParsedPage, build_chunks, clean_text, tokenise_for_search


def test_clean_text_removes_blank_lines_and_nbsp() -> None:
    assert clean_text("  第一行\u00a0\n\n 第二行  \n") == "第一行\n第二行"


def test_chunks_keep_table_as_one_evidence_unit() -> None:
    table = "| 项目 | 金额 |\n| --- | --- |\n| 募集资金 | 10亿元 |"
    chunks = build_chunks([ParsedPage(3, f"发行条款\n\n{table}", "ocr")], target_size=20, overlap=0)
    assert any(chunk.structure_type == "table" and "10亿元" in chunk.content for chunk in chunks)
    assert all(chunk.page_start == 3 and chunk.page_end == 3 for chunk in chunks)
    assert all(chunk.location.startswith("第 3 页") for chunk in chunks)


def test_chinese_tokenisation_produces_search_terms() -> None:
    terms = tokenise_for_search("募集资金用于偿还银行借款")
    assert "募集" in terms
    assert "资金" in terms
