from app.data.gateway import ColumnMetadata, TableMetadata
from app.semantic.entities import EntityResolver, normalize_value


def table(
    name: str,
    column: str,
    values: tuple[str, ...],
    *,
    schema: str = "analytics",
) -> TableMetadata:
    return TableMetadata(
        schema_name=schema,
        table_name=name,
        columns=[column],
        description=f"{name} fixture",
        column_metadata=[
            ColumnMetadata(
                name=column,
                data_type="VARCHAR",
                nullable=False,
                description="",
                observed_values=values,
                observed_values_source="fixture",
            ),
        ],
    )


DEPARTMENTS_A = table("departments", "name", ("Engineering", "Finance", "Sales"))
# A deliberately different schema: different table and column names, different
# values. Proves resolution follows the datasource, not a naming convention.
UNITS_B = table("business_units", "unit_name", ("Platform", "Revenue Ops"))


def test_resolves_an_exact_value_from_the_datasource() -> None:
    resolution = EntityResolver().resolve(
        user_text="Only Engineering",
        authorized_tables=[DEPARTMENTS_A],
    )

    match = resolution.resolved
    assert match is not None
    assert match.value == "Engineering"
    assert match.strategy == "exact"
    assert match.qualified_column == "analytics.departments.name"


def test_resolves_in_a_differently_named_schema() -> None:
    """The same code path works on a schema sharing no names with the first."""
    resolution = EntityResolver().resolve(
        user_text="show me Revenue Ops",
        authorized_tables=[UNITS_B],
    )

    match = resolution.resolved
    assert match is not None
    assert match.value == "Revenue Ops"
    assert match.table_name == "business_units"


def test_unknown_value_resolves_to_nothing_rather_than_guessing() -> None:
    resolution = EntityResolver().resolve(
        user_text="Only Marketing",
        authorized_tables=[DEPARTMENTS_A],
    )

    assert resolution.is_unresolved
    assert resolution.resolved is None


def test_resolution_never_returns_a_value_outside_the_datasource() -> None:
    """Every candidate must be a real observed value, never a coined one."""
    observed = set(DEPARTMENTS_A.column_metadata[0].observed_values)

    for text in ("Engineering", "Finance and Sales", "engineering dept", "Enginering"):
        resolution = EntityResolver().resolve(
            user_text=text, authorized_tables=[DEPARTMENTS_A]
        )
        for candidate in resolution.candidates:
            assert candidate.value in observed


# --------------------------------------------------------------------------
# Isolation: datasource A's values must never satisfy datasource B
# --------------------------------------------------------------------------


def test_values_do_not_leak_across_datasources() -> None:
    resolver = EntityResolver()

    # "Engineering" exists only in datasource A.
    in_b = resolver.resolve(user_text="Only Engineering", authorized_tables=[UNITS_B])
    # "Platform" exists only in datasource B.
    in_a = resolver.resolve(user_text="Only Platform", authorized_tables=[DEPARTMENTS_A])

    assert in_b.is_unresolved
    assert in_a.is_unresolved


def test_resolution_is_confined_to_the_authorized_tables_it_is_given() -> None:
    """Unauthorized tables are absent from the list, so their values cannot leak."""
    resolver = EntityResolver()
    restricted = resolver.resolve(
        user_text="Only Revenue Ops", authorized_tables=[DEPARTMENTS_A]
    )
    permitted = resolver.resolve(
        user_text="Only Revenue Ops", authorized_tables=[DEPARTMENTS_A, UNITS_B]
    )

    assert restricted.is_unresolved
    assert permitted.resolved is not None


def test_high_cardinality_columns_report_no_values_and_resolve_nothing() -> None:
    """A column past the cardinality bound reports (), keeping names out."""
    employees = table("employees", "full_name", ())

    resolution = EntityResolver().resolve(
        user_text="show me Dana Whitfield", authorized_tables=[employees]
    )

    assert resolution.is_unresolved


# --------------------------------------------------------------------------
# Resolution ladder
# --------------------------------------------------------------------------


def test_concept_narrows_resolution_to_the_backing_columns() -> None:
    resolver = EntityResolver()
    statuses = table("employees", "status", ("active", "terminated"))

    matched = resolver.resolve(
        user_text="Only Engineering",
        authorized_tables=[DEPARTMENTS_A, statuses],
        concept="department",
    )
    # The same text resolves to nothing under an unrelated concept.
    unmatched = resolver.resolve(
        user_text="Only Engineering",
        authorized_tables=[DEPARTMENTS_A, statuses],
        concept="status",
    )

    assert matched.resolved is not None
    assert matched.resolved.value == "Engineering"
    assert unmatched.is_unresolved


def test_prefix_resolution_finds_a_multiword_value_by_its_first_word() -> None:
    projects = table("projects", "name", ("Falcon Migration", "Atlas Rollout"))

    resolution = EntityResolver().resolve(
        user_text="Show Falcon's project margin", authorized_tables=[projects]
    )

    match = resolution.resolved
    assert match is not None
    assert match.value == "Falcon Migration"
    assert match.strategy == "prefix"


def test_fuzzy_resolution_tolerates_a_typo() -> None:
    resolution = EntityResolver().resolve(
        user_text="Only Enginering", authorized_tables=[DEPARTMENTS_A]
    )

    match = resolution.resolved
    assert match is not None
    assert match.value == "Engineering"
    assert match.strategy == "fuzzy"


def test_a_stronger_strategy_wins_over_a_weaker_one() -> None:
    """An exact match must not be diluted by fuzzy near-misses."""
    departments = table("departments", "name", ("Sales", "Sales Ops"))

    resolution = EntityResolver().resolve(
        user_text="Only Sales Ops", authorized_tables=[departments]
    )

    assert resolution.resolved is not None or resolution.is_ambiguous
    assert all(c.strategy == "exact" for c in resolution.candidates)
    assert resolution.candidates[0].value == "Sales Ops"


def test_ambiguity_is_reported_rather_than_silently_picked() -> None:
    projects = table("projects", "name", ("Falcon One", "Falcon Two"))

    resolution = EntityResolver().resolve(
        user_text="how did Falcon do", authorized_tables=[projects]
    )

    assert resolution.is_ambiguous
    assert resolution.resolved is None
    assert {c.value for c in resolution.candidates} == {"Falcon One", "Falcon Two"}


def test_empty_question_resolves_to_nothing() -> None:
    assert EntityResolver().resolve(
        user_text="   ", authorized_tables=[DEPARTMENTS_A]
    ).is_unresolved


def test_normalization_folds_case_accents_and_separators() -> None:
    assert normalize_value("People_Operations") == "people operations"
    assert normalize_value("Café") == "cafe"
    assert normalize_value("  Sales  Ops ") == "sales ops"
