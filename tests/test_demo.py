from app.services.demo import build_demo_materials
from app.config import Settings
from app.services.documents import DocumentParser


def test_demo_materials_are_parseable_docx() -> None:
    materials = build_demo_materials()
    assert len(materials) == 3
    parser = DocumentParser(Settings(database_url="postgresql+psycopg://test:test@localhost/test"))
    pages = parser.parse(materials[0].file_name, "application/vnd.openxmlformats-officedocument.wordprocessingml.document", materials[0].content)
    assert pages[0].extraction_method == "docx"
    assert "募集资金" in pages[0].content
