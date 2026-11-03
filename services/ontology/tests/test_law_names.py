from envcite_ontology.law_names import LawNames

NAMES = LawNames(
    {
        "水污染防治法": ["O0040001"],
        "水污染防治法施行細則": ["O0040002"],
        "空氣污染防制法": ["O0020001"],
        "民法": ["B0000001"],
        "舊名稱法": ["O0020001", "O0020009"],
    }
)


def test_a_named_law_is_found_wherever_it_appears() -> None:
    assert NAMES.find("事業依水污染防治法第26條被查證") == ["O0040001"]
    assert NAMES.find("空氣污染防制法第49條為什麼提到水污染防治法第19條") == [
        "O0020001",
        "O0040001",
    ]


def test_the_longest_name_wins_and_does_not_also_name_its_prefix() -> None:
    assert NAMES.find("水污染防治法施行細則第3條") == ["O0040002"]
    assert NAMES.find("水污染防治法施行細則與水污染防治法") == ["O0040001", "O0040002"]


def test_a_title_shared_by_laws_names_all_of_them() -> None:
    assert NAMES.find("舊名稱法的規定") == ["O0020001", "O0020009"]


def test_two_character_names_and_unnamed_questions_name_nothing() -> None:
    assert NAMES.find("民法的規定") == []
    assert NAMES.find("工廠排煙被檢舉會怎樣") == []
