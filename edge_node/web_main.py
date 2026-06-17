import uvicorn

from edge_node.infrastructure.fastapi.app import app


def main():
    uvicorn.run(app, host="0.0.0.0", port=8001)


if __name__ == "__main__":
    main()
