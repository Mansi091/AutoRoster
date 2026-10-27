"""Run the AutoRoster FastAPI application with Uvicorn."""

if __name__ == "__main__":
    import uvicorn

    uvicorn.run("backend.app.main:app", host="0.0.0.0", port=5000, reload=True)
