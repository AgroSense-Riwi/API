def main() -> None:
    import uvicorn

    uvicorn.run("agrosense_api.script:app", host="127.0.0.1", port=8000)
