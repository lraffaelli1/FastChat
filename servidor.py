# server.py (con historial en %TEMP% arreglado)
import asyncio, json, datetime, pathlib, logging, os, tempfile
import websockets
from websockets.exceptions import ConnectionClosedOK, ConnectionClosedError
from collections import deque

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

CLIENTS = set()
HISTORY_MAX = 30
HISTORY_PATH = pathlib.Path(tempfile.gettempdir()) / "fastchat.json"   # 👈 ahora es Path
HISTORY = deque(maxlen=HISTORY_MAX)


async def safe_send(ws, data: str):
    try:
        await ws.send(data)
        return True
    except Exception as e:
        logging.warning(f"Cliente eliminado por error de envío: {e!r}")
        try: await ws.close()
        except Exception: pass
        return False

async def broadcast(msg: dict, sender_ws=None):
    if not CLIENTS: return
    data = json.dumps(msg, ensure_ascii=False)
    dead = []
    for ws in list(CLIENTS):
        if ws is sender_ws:   # no reenvíes al emisor
            continue
        ok = await safe_send(ws, data)
        if not ok:
            dead.append(ws)
    for ws in dead:
        CLIENTS.discard(ws)

async def send_history(ws):
    if not HISTORY:
        return
    payload = {"type": "history", "items": list(HISTORY)}
    await safe_send(ws, json.dumps(payload, ensure_ascii=False))

async def handler(ws):
    CLIENTS.add(ws)
    peer = getattr(ws, "remote_address", None)
    logging.info(f"Cliente conectado: {peer}")

    # Enviar historial al conectarse
    await send_history(ws)

    try:
        async for raw in ws:
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                logging.debug("Mensaje no-JSON ignorado")
                continue

            text = msg.get("text", "")
            sender = msg.get("from", "???")
            attachments = msg.get("attachments", [])

            norm = {
                "type": "image" if attachments else "msg",
                "from": sender,
                "text": text,
                "attachments": attachments,   # 👈 reenviamos adjuntos
                "ts": datetime.datetime.now().isoformat(timespec="seconds"),
            }

            HISTORY.append(norm)
            save_history()
            await broadcast(norm, sender_ws=ws)

    except (ConnectionClosedOK, ConnectionClosedError) as e:
        logging.info(f"Cliente desconectado ({peer}): {e}")
    except OSError as e:
        logging.info(f"Cliente desconectado por OSError ({peer}): {e}")
    except asyncio.CancelledError:
        raise
    except Exception as e:
        logging.exception(f"Error en handler ({peer}): {e}")
    finally:
        CLIENTS.discard(ws)

def load_history():
    if HISTORY_PATH.exists():
        try:
            items = json.loads(HISTORY_PATH.read_text(encoding="utf-8"))
            for it in items[-HISTORY_MAX:]:
                HISTORY.append(it)
            logging.info(f"Historial cargado: {len(HISTORY)} mensajes.")
        except Exception as e:
            logging.warning(f"No se pudo cargar {HISTORY_PATH}: {e}")

def save_history():
    try:
        HISTORY_PATH.write_text(
            json.dumps(list(HISTORY), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception as e:
        logging.warning(f"No se pudo guardar {HISTORY_PATH}: {e}")

async def main():
    load_history()  # cargar historial desde %TEMP%
    host = "0.0.0.0"; port = 8765
    logging.info(f"Levantando servidor en ws://{host}:{port}")
    async with websockets.serve(
        handler, host=host, port=port,
        ping_interval=20, ping_timeout=20, max_queue=32, close_timeout=5
    ):
        logging.info("Servidor listo. Conecta los clientes a 8765")
        await asyncio.Future()  # correr para siempre

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logging.info("Servidor detenido por teclado.")
