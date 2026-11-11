# 体检夹具：只保留和端口有关的几行，导入或执行它都不会监听端口。
BREEZE = "http://127.0.0.1:8137"


def serve(port, handler):
    http.server.ThreadingHTTPServer(("127.0.0.1", port), handler).serve_forever()


if __name__ == "__main__":
    import threading
    threading.Thread(target=serve, args=(18765, None), daemon=True).start()
    serve(8138, None)
