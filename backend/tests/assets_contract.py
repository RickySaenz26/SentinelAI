"""Extract the assets contract; normalize only the configurable CSRF header."""

from copy import deepcopy


def assets_contract(
    document: dict,
    csrf_header: str,
    *,
    prefix="/api/v1/assets",
    title="SentinelAI laboratory assets — increment 1",
) -> dict:
    paths = deepcopy(
        {key: value for key, value in document["paths"].items() if key.startswith(prefix)}
    )
    references: set[str] = set()

    def inspect(value):
        if isinstance(value, dict):
            if "$ref" in value:
                references.add(value["$ref"].rsplit("/", 1)[-1])
            if value.get("in") == "header" and value.get("name") == csrf_header:
                value["name"] = "X-CSRF-Token"
                value["schema"]["title"] = "X-Csrf-Token"
            for nested in value.values():
                inspect(nested)
        elif isinstance(value, list):
            for nested in value:
                inspect(nested)

    inspect(paths)
    schemas = {}
    while missing := references - schemas.keys():
        for name in sorted(missing):
            schemas[name] = deepcopy(document["components"]["schemas"][name])
            inspect(schemas[name])
    security_names = {
        name
        for operations in paths.values()
        for operation in operations.values()
        for requirement in operation.get("security", [])
        for name in requirement
    }
    components = {"schemas": schemas}
    if security_names:
        components["securitySchemes"] = {
            name: deepcopy(document["components"]["securitySchemes"][name])
            for name in sorted(security_names)
        }
    return {
        "openapi": document["openapi"],
        "info": {"title": title, "version": "1.0.0"},
        "paths": paths,
        "components": components,
    }
