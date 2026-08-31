package enterprise.analytics

import rego.v1

default allow := false
default debug_allowed := false

# Authority over what the data is defined to mean, which is separate from
# permission to read it. An analyst grant never carries this.
default knowledge_review_allowed := false

matching_grants := [grant |
    some role in input.identity.roles
    grant := data.roles[role]
]

allow if {
    input.operation == "analytics.query"
    count(matching_grants) > 0
}

schema_allowed_by_grant(grant, schema) if "*" in grant.schemas
schema_allowed_by_grant(grant, schema) if schema in grant.schemas

table_allowed(schema, identifier) if {
    some grant in matching_grants
    table_allowed_by_grant(grant, schema, identifier)
}

column_allowed(schema, identifier, column) if {
    some grant in matching_grants
    table_allowed_by_grant(grant, schema, identifier)
    not column_denied_by_grant(grant, identifier, column)
}

table_allowed_by_grant(grant, schema, identifier) if {
    schema_allowed_by_grant(grant, schema)
    "*" in grant.tables
}

table_allowed_by_grant(grant, schema, identifier) if {
    schema_allowed_by_grant(grant, schema)
    identifier in grant.tables
}

column_denied_by_grant(grant, identifier, column) if {
    columns := object.get(grant.denied_columns, identifier, [])
    column in columns
}

column_denied_by_grant(grant, identifier, column) if {
    columns := object.get(grant.denied_columns, "*", [])
    column in columns
}

metric_allowed(metric) if {
    some grant in matching_grants
    "*" in grant.metrics
}

metric_allowed(metric) if {
    some grant in matching_grants
    metric in grant.metrics
}

allowed_tables := {table.identifier: [column |
    some column in table.columns
    column_allowed(table.schema, table.identifier, column)
] |
    some table in input.resources.tables
    table_allowed(table.schema, table.identifier)
}

allowed_schemas := {table.schema |
    some table in input.resources.tables
    table_allowed(table.schema, table.identifier)
}

allowed_metrics := {metric |
    some metric in input.resources.metrics
    metric_allowed(metric)
}

debug_allowed if {
    "debug_provenance" in input.resources.capabilities
    some grant in matching_grants
    object.get(grant, "debug", false)
}

knowledge_review_allowed if {
    some grant in matching_grants
    object.get(grant, "knowledge_review", false)
}

decision := {
    "allow": allow,
    "allowed_schemas": allowed_schemas,
    "allowed_tables": allowed_tables,
    "allowed_metrics": allowed_metrics,
    "debug_allowed": debug_allowed,
    "knowledge_review_allowed": knowledge_review_allowed,
}
