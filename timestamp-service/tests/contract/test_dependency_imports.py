def test_runtime_and_test_dependencies_import() -> None:
    import alembic
    import fastapi
    import httpx
    import jsonschema
    import opentimestamps
    import psycopg
    import pydantic
    import pydantic_settings
    import sqlalchemy
    import stripe
    import uvicorn

    assert all(
        module is not None
        for module in (
            alembic,
            fastapi,
            httpx,
            jsonschema,
            opentimestamps,
            psycopg,
            pydantic,
            pydantic_settings,
            sqlalchemy,
            stripe,
            uvicorn,
        )
    )
