import sys, asyncio, threading, json, winsound, pathlib, os
from PyQt6 import QtWidgets, QtGui, QtCore
import websockets, base64, html, tempfile, datetime, logging
from io import BytesIO; from collections import deque

APP_ORG = "Tecnicos"
APP_NAME = "FastChat"



def resource_path(rel_path: str) -> str:
    base = getattr(sys, "_MEIPASS", os.path.abspath("."))
    return os.path.join(base, rel_path)

def play_sound(path: str, fallback: str):
    try:
        snd = (path or "").strip()
        if not snd or not pathlib.Path(snd).exists():
            snd = resource_path(fallback)
        if snd and pathlib.Path(snd).exists():
            winsound.PlaySound(snd, winsound.SND_FILENAME | winsound.SND_ASYNC)
    except Exception:
        pass

def _qimage_to_png_bytes(img: QtGui.QImage) -> bytes:
    ba = QtCore.QByteArray()
    buff = QtCore.QBuffer(ba)
    buff.open(QtCore.QIODevice.OpenModeFlag.WriteOnly)
    img.save(buff, "PNG")
    return bytes(ba)

def _pixmap_from_path(path: str) -> QtGui.QPixmap | None:
    if not pathlib.Path(path).exists():
        return None
    pm = QtGui.QPixmap(path)
    return pm if not pm.isNull() else None

def _esc(s: str) -> str:
    return html.escape(s, quote=True)

def _img_tag_from_bytes(b: bytes, mime="image/png") -> str:
    b64 = base64.b64encode(b).decode("ascii")
    data = f"data:{mime};base64,{b64}"
    return (
        '<div style="margin:6px 0;">'
        f'  <a href="{data}">'
        f'    <img src="{data}" '
        '         style="max-width: 320px; max-height: 220px; '
        '                border:1px solid #2A2F36; border-radius:8px;">'
        '  </a>'
        '</div>'
    )

import tempfile, pathlib, html

def _esc(s: str) -> str:
    return html.escape(s, quote=True)

def _save_temp_image(b: bytes, name="imagen.png") -> str:
    tmpdir = pathlib.Path(tempfile.gettempdir()) / "FastChat"
    tmpdir.mkdir(exist_ok=True)
    base = pathlib.Path(name).stem or "imagen"
    ext = pathlib.Path(name).suffix or ".png"
    i = 0
    while True:
        candidate = tmpdir / (f"{base}{'' if i==0 else f'_{i}'}{ext}")
        if not candidate.exists():
            break
        i += 1
    candidate.write_bytes(b)
    return str(candidate)

def _thumb_html_for_file(path: str, name: str, max_w=320, max_h=220) -> str:
    url = QtCore.QUrl.fromLocalFile(path).toString()  # file:///...
    return (
        f'<a href="{url}">'
        f'  <img src="{url}" '
        f'       style="max-width:{max_w}px; max-height:{max_h}px; '
        '              border:1px solid #2A2F36; border-radius:8px;">'
        '</a>'
    )


def _img_link_from_bytes(b: bytes, mime: str = "image/png", name: str = "imagen.png") -> str:
    path = _save_temp_image(b, name)
    url = QtCore.QUrl.fromLocalFile(path).toString() 
    return f'📷 {_esc(name)} — <a href="{url}">Abrir</a>'



# ---------- Utilidades ----------
class Settings:
    def __init__(self):
        self.qs = QtCore.QSettings(APP_ORG, APP_NAME)
        # Defaults
        self.server_url = self.qs.value("server_url", "ws://127.0.0.1:8765", str)
        self.user_name  = self.qs.value("user_name",  "Usuario", str)
        self.sound_path = self.qs.value("sound_path", resource_path("sound.wav"), str)
        self.sound_send = self.qs.value("sound_send", resource_path("send.wav"), str)
        self.sound_recive = self.qs.value("sound_recive", resource_path("recive.wav"), str)
        self.icon_path  = self.qs.value("icon_path",  resource_path("icon.ico"), str)
        self.icon_unread = self.qs.value("icon_unread", resource_path("icon_unread.ico"), str)
        self.toast_ms   = int(self.qs.value("toast_ms", 5000))
        self.password     = self.qs.value("password", "vna117sw.", str) 
        self.host_mode     = self.qs.value("host_mode", False, bool)
        self.admin_mode    = self.qs.value("admin_mode", False, bool)

    def save(self):
        self.qs.setValue("server_url", self.server_url)
        self.qs.setValue("user_name",  self.user_name)
        self.qs.setValue("sound_path", self.sound_path)
        self.qs.setValue("sound_send", self.sound_send)
        self.qs.setValue("sound_recive", self.sound_recive)
        self.qs.setValue("icon_path",  self.icon_path)
        self.qs.setValue("icon_unread", self.icon_unread)
        self.qs.setValue("toast_ms",   self.toast_ms)
        self.qs.setValue("password", self.password)
        self.qs.setValue("host_mode", self.host_mode)
        self.qs.setValue("admin_mode", self.admin_mode)
        self.qs.sync()

def prompt_password(correct: str, admin_mode: bool = False) -> bool:
    if admin_mode:
        return True
    if not correct:
        return True
    text, ok = QtWidgets.QInputDialog.getText(
        None, "FastChat", "Clave de Administrador:",
        QtWidgets.QLineEdit.EchoMode.Password
    )
    return ok and text == correct




# ---------- Diálogo elegante de respuesta (ya mejorado) ----------
class ReplyDialog(QtWidgets.QDialog):
    submitted = QtCore.pyqtSignal(str, list)

    def eventFilter(self, obj, event):
        if obj is self.input and event.type() == QtCore.QEvent.Type.KeyPress:
            # Enter / Shift+Enter
            if event.key() in (QtCore.Qt.Key.Key_Return, QtCore.Qt.Key.Key_Enter):
                if event.modifiers() & QtCore.Qt.KeyboardModifier.ShiftModifier:
                    return False  # permitir salto de línea
                self._on_send()
                return True

            # Escape → cerrar ventana
            if event.key() == QtCore.Qt.Key.Key_Escape:
                self.reject()
                return True

            # Ctrl+V → pegar imagen
            if event.matches(QtGui.QKeySequence.StandardKey.Paste):
                md = QtGui.QGuiApplication.clipboard().mimeData()
                if md.hasImage():
                    img = md.imageData()
                    if isinstance(img, QtGui.QImage):
                        self._insert_inline_image(img)  # 👈 ahora va inline en el input
                        return True
                return False


        return super().eventFilter(obj, event)



    def __init__(self, parent=None, title="Responder", history=None, close_on_send=False):
        super().__init__(parent)
        self.attachments = []
        self.setAcceptDrops(True)
        self.close_on_send = close_on_send
        self._img_seq = 0 

        QtGui.QShortcut(QtGui.QKeySequence("Ctrl+Return"), self, activated=self._on_send)
        QtGui.QShortcut(QtGui.QKeySequence("Ctrl+Enter"),  self, activated=self._on_send)

        self.setWindowTitle(title)
        self.setWindowFlag(QtCore.Qt.WindowType.FramelessWindowHint, True)
        self.setWindowFlag(QtCore.Qt.WindowType.Tool, True)
        self.setWindowFlag(QtCore.Qt.WindowType.WindowStaysOnTopHint, True)
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_TranslucentBackground, True)

        card = QtWidgets.QFrame(objectName="card")
        card.setStyleSheet("""
            #card { background:#111316; color:#EAECEF; border-radius:14px; }
            QLabel#title { font-size:14px; font-weight:600; color:#EAECEF; }
            QTextEdit { background:#1A1D21; border:1px solid #2A2F36; border-radius:10px; padding:10px 12px; color:#EAECEF; selection-background-color:#FF9A2DFF; }
            QPushButton { border:none; border-radius:10px; padding:8px 14px; background:#2A2F36; color:#EAECEF; }
            QPushButton:hover { background:#343A42; }
            QPushButton#primary { background:#FF9A2D; color:white; }
            QPushButton#primary:hover { background:#FFB41F; }
        """)

        shadow = QtWidgets.QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(100); shadow.setXOffset(0); shadow.setYOffset(3)
        shadow.setColor(QtGui.QColor(0,0,0,30))
        card.setGraphicsEffect(shadow)

        icon_lbl = QtWidgets.QLabel(self); icon_lbl.setFixedWidth(22)
        imagen = QtGui.QPixmap(resource_path('icon.png'))
        icon_lbl.setPixmap(imagen.scaled(22, 22, QtCore.Qt.AspectRatioMode.KeepAspectRatio, QtCore.Qt.TransformationMode.SmoothTransformation))
        title_lbl = QtWidgets.QLabel("🗲 FastChat", objectName="title")
        close_btn = QtWidgets.QPushButton("✕")
        close_btn.setStyleSheet("QPushButton { background:transparent; } QPushButton:hover { background:#1F2328; }")
        close_btn.clicked.connect(self.reject)

        header = QtWidgets.QHBoxLayout(); header.setContentsMargins(0,0,0,0); header.setSpacing(8)
        header.addWidget(icon_lbl); header.addStretch(1); header.addWidget(title_lbl); header.addStretch(1); header.addWidget(close_btn)

        self.history_box = QtWidgets.QTextBrowser()
        self.history_box.setObjectName("history")
        self.history_box.setFixedHeight(200)
        self.history_box.setOpenExternalLinks(False)
        self.history_box.setOpenLinks(False)
        self.history_box.anchorClicked.connect(lambda url: QtGui.QDesktopServices.openUrl(url))
        self.history_box.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.history_box.setStyleSheet("""
            /* caja */
            QTextBrowser#history {
                background: #1A1D21;
                border: 1px solid #2A2F36;
                border-radius: 10px;
                padding: 8px 10px 8px 10px;   /* un poco de padding para respirar */
                color: #EAECEF;
            }
            /* links y selección */
            QTextBrowser#history a { color: #FF9A2D; text-decoration: none; }
            QTextBrowser#history a:hover { text-decoration: underline; }
            QTextBrowser#history { selection-background-color: #FF9A2D33; }

            /* ===== Scrollbar vertical moderno ===== */
            QTextBrowser#history QScrollBar:vertical {
                background: transparent;
                width: 10px;                 /* fino */
                margin: 8px 2px 8px 0;       /* separa del borde derecho */
                border: none;
            }
            QTextBrowser#history QScrollBar::handle:vertical {
                background: #3A414A;         /* rail neutral */
                min-height: 24px;
                border-radius: 6px;
            }
            QTextBrowser#history QScrollBar::handle:vertical:hover {
                background: #4A515A;
            }
            QTextBrowser#history QScrollBar::add-line:vertical,
            QTextBrowser#history QScrollBar::sub-line:vertical {
                height: 0px;                 /* sin flechas */
                border: none;
                background: transparent;
            }
            QTextBrowser#history QScrollBar::add-page:vertical,
            QTextBrowser#history QScrollBar::sub-page:vertical {
                background: transparent;     /* sin bloques raros */
            }

            /* ===== (opcional) Scrollbar horizontal oculto ===== */
            QTextBrowser#history QScrollBar:horizontal { height: 0px; }
        """)
        if history:
            self.set_history(history)

        self.input = QtWidgets.QTextEdit()
        self.input.setPlaceholderText("Escribe..."); self.input.setStyleSheet("font-size:15px;")
        self.input.setAlignment(QtCore.Qt.AlignmentFlag.AlignVCenter)
        self.input.setFixedHeight(50)
        self.input.installEventFilter(self)

        send_btn = QtWidgets.QPushButton("➤", objectName="primary")
        send_btn.clicked.connect(self._on_send)
        send_btn.setFixedHeight(50); send_btn.setStyleSheet("font-size:24px;")

        buttons = QtWidgets.QHBoxLayout(); buttons.addWidget(self.input); buttons.addWidget(send_btn)

        v = QtWidgets.QVBoxLayout(card); v.setContentsMargins(14,12,12,12); v.setSpacing(12)
        v.addLayout(header); v.addWidget(self.history_box); v.addLayout(buttons)

        

        root = QtWidgets.QVBoxLayout(self); root.setContentsMargins(8,8,8,8); root.addWidget(card)
        self.resize(480, 180)

        QtGui.QShortcut(QtGui.QKeySequence.StandardKey.Close, self, activated=self.reject)
        QtGui.QShortcut(QtGui.QKeySequence(QtCore.Qt.Key.Key_Escape), self, activated=self.reject)
        self.setFocusPolicy(QtCore.Qt.FocusPolicy.StrongFocus)
        self.input.setFocusPolicy(QtCore.Qt.FocusPolicy.StrongFocus)


    # ---------- Drag & Drop ----------
    def dragEnterEvent(self, e: QtGui.QDragEnterEvent):
        if e.mimeData().hasImage() or e.mimeData().hasUrls():
            e.acceptProposedAction()
        else:
            e.ignore()

    def dropEvent(self, e: QtGui.QDropEvent):
        md = e.mimeData()
        if md.hasImage():
            img = md.imageData()
            if isinstance(img, QtGui.QImage):
                self._insert_inline_image(img)
        if md.hasUrls():
            for u in md.urls():
                path = u.toLocalFile()
                pm = _pixmap_from_path(path)
                if pm:
                    self._insert_inline_image(pm.toImage())
        e.acceptProposedAction()


    def _add_qimage(self, img: QtGui.QImage, name="image.png"):
        b = _qimage_to_png_bytes(img)
        self.attachments.append({"name": name, "bytes": b, "mime": "image/png"})

    def _on_send(self):
        text = self.input.toPlainText().strip()

        doc = self.input.document()
        img_attachments = []
        block = doc.begin()
        while block.isValid():
            it = block.begin()
            while not it.atEnd():
                frag = it.fragment()
                if frag.isValid():
                    cf = frag.charFormat()
                    if cf.isImageFormat():
                        img_fmt = cf.toImageFormat()
                        name = img_fmt.name()
                        qimg = doc.resource(
                            QtGui.QTextDocument.ResourceType.ImageResource,
                            QtCore.QUrl(name)
                        )
                        if isinstance(qimg, QtGui.QImage):
                            qimg = doc.resource(QtGui.QTextDocument.ResourceType.ImageResource, QtCore.QUrl(name))
                            b = _qimage_to_png_bytes(qimg)
                            img_attachments.append({
                                "type": "image",
                                "name": name.split("/")[-1] or "image.png",
                                "mime": "image/png",
                                "bytes": b,
                            })
                it += 1
            block = block.next()


        # 3) Emitir (adjuntos como bytes; quien llama arma base64)
        if text or img_attachments:
            self.submitted.emit(text, img_attachments)

        # 4) Limpiar input y reiniciar contador
        self.input.clear()
        self._img_seq = 0


    def quit(self):
        inst = QtWidgets.QApplication.instance()
        if inst:
            inst.quit()

    def showEvent(self, e: QtGui.QShowEvent) -> None:
        super().showEvent(e)
        from PyQt6.QtCore import QTimer
        QTimer.singleShot(0, lambda: self.history_box.verticalScrollBar().setValue(
            self.history_box.verticalScrollBar().maximum()
        ))
        self.input.setFocus(QtCore.Qt.FocusReason.ActiveWindowFocusReason)
        self._move_bottom_right(20, 20)

    def _move_bottom_right(self, margin_x=20, margin_y=20):
        screen = QtGui.QGuiApplication.screenAt(QtGui.QCursor.pos()) or QtGui.QGuiApplication.primaryScreen()
        rect = screen.availableGeometry()
        self.adjustSize(); geo = self.frameGeometry()
        x = rect.right() - geo.width() - margin_x
        y = rect.bottom() - geo.height() - margin_y
        self.move(x, y)

    def set_history(self, msgs, max=30):
        # msgs: list[tuple[str, str_html]]
        html_hist = "".join(f"<b> - {_esc(s)}: </b> {h}<br>" for s, h in msgs[-max:])
        self.history_box.setHtml(html_hist)
        self.history_box.verticalScrollBar().setValue(
            self.history_box.verticalScrollBar().maximum()
        )
        
    def _insert_inline_image(self, img: QtGui.QImage, max_w=320, max_h=220):
        # Tamaño original
        ow, oh = img.width(), img.height()
        # Escala SOLO para mostrar (no tocamos los píxeles originales)
        scale = min(max_w / ow, max_h / oh, 1.0)
        disp_w = int(ow * scale)
        disp_h = int(oh * scale)

        # Recurso único en el documento (guardamos la imagen ORIGINAL)
        name = f"inline://img{self._img_seq}.png"
        self._img_seq += 1

        doc = self.input.document()
        doc.addResource(QtGui.QTextDocument.ResourceType.ImageResource,
                        QtCore.QUrl(name), img)  # 👈 original, sin reducir

        # Formato con tamaño de presentación reducido
        fmt = QtGui.QTextImageFormat()
        fmt.setName(name)
        fmt.setWidth(disp_w)
        fmt.setHeight(disp_h)

        # Insertar en el cursor
        cursor = self.input.textCursor()
        cursor.insertImage(fmt)


# ---------- Diálogo de Configuración ----------
class SettingsDialog(QtWidgets.QDialog):
    saved = QtCore.pyqtSignal(Settings)  # emite settings nuevos

    def __init__(self, settings: Settings, parent=None):
        super().__init__(parent)
        self.setWindowIcon(QtGui.QIcon(resource_path("icon.ico")))
        self.setWindowTitle("Configuración")
        self.setModal(True)
        self.settings = settings

        tabs = QtWidgets.QTabWidget()
        tabs.addTab(self._general_tab(), "General")

        btn_save = QtWidgets.QPushButton("Guardar")
        btn_save.clicked.connect(self._on_save)

        btns = QtWidgets.QHBoxLayout()
        btns.addStretch(1)
        btns.addWidget(btn_save)

        lay = QtWidgets.QVBoxLayout(self)
        lay.addWidget(tabs)
        lay.addLayout(btns)
        self.resize(400, 150)
    

    def _general_tab(self):
        w = QtWidgets.QWidget()
        form = QtWidgets.QFormLayout()
        form.setLabelAlignment(QtCore.Qt.AlignmentFlag.AlignRight)

        self.ed_url = QtWidgets.QLineEdit(self.settings.server_url)
        self.ed_url.setPlaceholderText("ws://192.168.0.10:8765")
        self.ed_url.setToolTip("Dirección del servidor al que se conectará el cliente.")
        self.ed_user = QtWidgets.QLineEdit(self.settings.user_name)

        self.toggleServer = QtWidgets.QCheckBox("Modo Host")
        self.toggleServer.setToolTip("Si está activado, el cliente actuará como servidor con la ip asignada al equipo.")
        self.toggleServer.setChecked(self.settings.host_mode)

        self.toggleCliente = QtWidgets.QCheckBox("Administrador")
        self.toggleCliente.setToolTip("Si está activado, el cliente tendrá acceso a todos los chats.")
        self.toggleCliente.setChecked(self.settings.admin_mode)

        self.grid = QtWidgets.QGridLayout()
        self.grid.addWidget(self.toggleCliente, 0, 0)
        self.grid.addWidget(self.toggleServer, 0, 1)

        form.addRow("", self.grid)
        form.addRow("Usuario:", self.ed_user)
        form.addRow("Servidor:", self.ed_url)

        w.setLayout(form)
        return w


    def _on_save(self):
        url = self.ed_url.text().strip()
        user = self.ed_user.text().strip() or "Usuario"

        # Validación mínima
        if not (url.startswith("ws://") or url.startswith("wss://")):
            QtWidgets.QMessageBox.warning(
                self, "Dato inválido", "La URL del servidor debe empezar con ws:// o wss://"
            )
            return

        self.settings.server_url = url
        self.settings.user_name = user
        self.settings.host_mode = self.toggleServer.isChecked()
        self.settings.admin_mode = self.toggleCliente.isChecked()
        self.settings.save()

        self.saved.emit(self.settings)
        self.accept()

    


# ---------- Cliente en bandeja ----------
class TrayClient(QtWidgets.QSystemTrayIcon):
    message_received = QtCore.pyqtSignal(str, str)  # from, text

    def __init__(self, app: QtWidgets.QApplication, settings: Settings):
        self.reply_dialog = None
        self.settings = settings

        # Cargamos ambos íconos y dejamos uno por defecto
        self.icon_normal = QtGui.QIcon(self.settings.icon_path) if pathlib.Path(self.settings.icon_path).exists() \
                           else app.style().standardIcon(QtWidgets.QStyle.StandardPixmap.SP_ComputerIcon)
        self.icon_unread = QtGui.QIcon(self.settings.icon_unread) if pathlib.Path(self.settings.icon_unread).exists() \
                           else self.icon_normal

        self.unread_count = 0  # 👈 contador de no leídos

        super().__init__(self.icon_normal, app)
        self.app = app
        self.setToolTip("🗲 FastChat")
        self.activated.connect(self._on_tray_activated)
        self.messageClicked.connect(self._on_message_clicked)

        self.last_msgs = []

        # Menú de bandeja
        menu = QtWidgets.QMenu()
        send_action = menu.addAction("Enviar mensaje…")
        send_action.triggered.connect(self.prompt_and_send)
        cfg_action = menu.addAction("Configurar…")
        cfg_action.triggered.connect(self.open_settings)
        menu.addSeparator()
        quit_action = menu.addAction("Salir")
        quit_action.triggered.connect(self._quit_app)
        self.setContextMenu(menu)

        # Señal de mensaje entrante
        self.message_received.connect(self._show_notification)

        self.show()

        # Hilo WebSocket
        self.ws_thread = threading.Thread(target=self._ws_thread_main, daemon=True)
        self.ws_thread.start()
        self._host_running = False
        self._host_running = False
        self._host_clients = set()
        self._host_history = deque(maxlen=30)
        self._host_history_path = pathlib.Path(tempfile.gettempdir()) / "fastchat.json"

        # aplicar modo host luego de que el loop esté vivo
        QtCore.QTimer.singleShot(300, self._apply_host_mode_from_settings)

    def _apply_host_mode_from_settings(self):
        # sincronizamos en el loop asyncio
        asyncio.run_coroutine_threadsafe(self._host_apply_from_settings(), self.loop)
    
    async def _host_apply_from_settings(self):
        if self.settings.host_mode and not self._host_running:
            await self._host_start()
            # si activamos host, conectamos el cliente a localhost
            if not self.settings.server_url.startswith("ws://127.0.0.1"):
                self.settings.server_url = "ws://127.0.0.1:8765"
                self.settings.save()
                await self._force_reconnect()
        elif not self.settings.host_mode and self._host_running:
            await self._host_stop()

    async def _host_start(self, host="0.0.0.0", port=8765):
        if self._host_running:
            return
        # cargar historial desde disco (si existe)
        try:
            if self._host_history_path.exists():
                items = json.loads(self._host_history_path.read_text(encoding="utf-8"))
                for it in items[-30:]:
                    self._host_history.append(it)
                print(f"[Host] Historial cargado ({len(self._host_history)} mensajes)")
        except Exception as e:
            print(f"[Host] No se pudo cargar historial: {e}")

        try:
            self._host_wsserver = await websockets.serve(
                self._host_handler, host=host, port=port,
                ping_interval=20, ping_timeout=20, max_queue=32, close_timeout=5
            )
            self._host_running = True
            print(f"[Host] Servidor WebSocket en ws://{host}:{port}")
        except OSError as e:
            print(f"[Host] No se pudo iniciar servidor en {port}: {e}")
            self.settings.host_mode = False
            self.settings.save()

    async def _host_stop(self):
        if not self._host_running:
            return
        try:
            self._host_wsserver.close()
            await self._host_wsserver.wait_closed()
            print("[Host] Servidor detenido")
        except Exception as e:
            print(f"[Host] Error al detener servidor: {e}")
        finally:
            self._host_running = False
            self._host_clients.clear()

    async def _host_handler(self, ws):
        self._host_clients.add(ws)
        try:
            # enviar historial a quien entra
            if self._host_history:
                payload = {"type": "history", "items": list(self._host_history)}
                await self._host_safe_send(ws, json.dumps(payload, ensure_ascii=False))

            async for raw in ws:
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    continue

                text = msg.get("text", "")
                sender = msg.get("from", "???")
                attachments = msg.get("attachments", [])

                norm = {
                    "type": "image" if attachments else "msg",
                    "from": sender,
                    "text": text,
                    "attachments": attachments,
                    "ts": datetime.datetime.now().isoformat(timespec="seconds"),
                }

                self._host_history.append(norm)
                # persistir a disco
                try:
                    self._host_history_path.write_text(
                        json.dumps(list(self._host_history), ensure_ascii=False, indent=2),
                        encoding="utf-8"
                    )
                except Exception as e:
                    print(f"[Host] No se pudo guardar historial: {e}")

                # broadcast a todos excepto el emisor
                await self._host_broadcast(norm, sender_ws=ws)
        finally:
            self._host_clients.discard(ws)

    async def _host_broadcast(self, msg: dict, sender_ws=None):
        if not self._host_clients:
            return
        data = json.dumps(msg, ensure_ascii=False)
        dead = []
        for c in list(self._host_clients):
            if c is sender_ws:
                continue
            ok = await self._host_safe_send(c, data)
            if not ok:
                dead.append(c)
        for d in dead:
            try:
                self._host_clients.discard(d)
            except Exception:
                pass

    async def _host_safe_send(self, ws, data: str) -> bool:
        try:
            await ws.send(data)
            return True
        except Exception:
            try:
                await ws.close()
            except Exception:
                pass
            return False



        

    # ---------- Helpers de icono/no leídos ----------
    def _update_tray_icon(self):
        """Elige el ícono según si hay no leídos."""
        if self.unread_count > 0:
            self.setIcon(self.icon_unread)
            self.setToolTip(f"🗲 FastChat — {self.unread_count} sin leer")
        else:
            self.setIcon(self.icon_normal)
            self.setToolTip("🗲 FastChat")

    def _mark_all_read(self):
        """Resetea contador de no leídos y vuelve a icono normal."""
        self.unread_count = 0
        self._update_tray_icon()

    # ---------- Notificaciones ----------
    def _show_notification(self, sender: str, text: str):

        if self.reply_dialog and self.reply_dialog.isVisible():
            self.reply_dialog.set_history(self.last_msgs)
            play_sound(self.settings.sound_recive, "recive.wav")
            return

        self.unread_count += 1
        self._update_tray_icon()
        play_sound(self.settings.sound_path, "sound.wav")
        self.showMessage("🗲 Nuevo Mensaje", f"{sender}: {text}", self.icon(), self.settings.toast_ms)


    def _on_message_clicked(self):
        # Al hacer click en el toast, abrimos y marcamos como leído
        self.prompt_and_send()

    def _on_tray_activated(self, reason: QtWidgets.QSystemTrayIcon.ActivationReason):
        if reason == QtWidgets.QSystemTrayIcon.ActivationReason.Trigger:
            self.prompt_and_send()

    # ---------- Envío ----------
    def prompt_and_send(self):
        self._mark_all_read()
        dlg = ReplyDialog(history=self.last_msgs, close_on_send=False)
        self.reply_dialog = dlg

        def send_and_append(text, attachments):
            payload = {
                "from": self.settings.user_name,
                "type": "msg" if text else "image",
                "text": text,
                "attachments": [
                    {"type":"image","name":a["name"],"mime":a["mime"],
                    "data": base64.b64encode(a["bytes"]).decode("ascii")}
                    for a in attachments
                ]
            }
            asyncio.run_coroutine_threadsafe(self._send_ws_payload(payload), self.loop)

            # --- construir HTML para historial ---
            html_parts = []
            if text:
                html_parts.append(_esc(text))
            for a in attachments:
                try:
                    fname = a.get("name", "imagen.png")
                    fpath = _save_temp_image(a["bytes"], fname)              # guarda a %TEMP%/FastChat
                    html_parts.append(_thumb_html_for_file(fpath, fname))    # miniatura clickeable
                except Exception:
                    pass
            shown_html = "<br>".join(html_parts) if html_parts else _esc("")


            self.last_msgs.append((self.settings.user_name, shown_html))
            dlg.set_history(self.last_msgs)
            play_sound(self.settings.sound_send, "send.wav")


        dlg.submitted.connect(send_and_append)
        dlg.exec()

        


    # ---------- Configuración ----------
    def open_settings(self):
        if not prompt_password(self.settings.password, self.settings.admin_mode):
            QtWidgets.QMessageBox.warning(None, "Acceso denegado", "Clave incorrecta.")
            return
        
        dlg = SettingsDialog(Settings(), None)

        def apply_and_refresh(st: Settings):
            self.settings = st
            self.icon_normal = QtGui.QIcon(self.settings.icon_path) if pathlib.Path(self.settings.icon_path).exists() \
                               else self.icon_normal
            self.icon_unread = QtGui.QIcon(self.settings.icon_unread) if pathlib.Path(self.settings.icon_unread).exists() \
                               else self.icon_normal
            self._update_tray_icon()
            asyncio.run_coroutine_threadsafe(self._force_reconnect(), self.loop)
            if self.settings.host_mode and not self.settings.server_url.startswith("ws://127.0.0.1"):
                self.settings.server_url = "ws://127.0.0.1:8765"
                self.settings.save()
                asyncio.run_coroutine_threadsafe(self._force_reconnect(), self.loop)
            else:
                asyncio.run_coroutine_threadsafe(self._force_reconnect(), self.loop)

        dlg.saved.connect(apply_and_refresh)
        dlg.exec()


    # ---------- Salir ----------
    def _quit_app(self):
        inst = QtWidgets.QApplication.instance()
        if inst:
            inst.quit()

    # ---------- WebSocket ----------
    async def _send_ws(self, text: str):
        try:
            if self.ws:
                await self.ws.send(json.dumps(
                    {"from": self.settings.user_name, "text": text},
                    ensure_ascii=False
                ))
            else:
                print("⚠️ No hay conexión WebSocket activa")
        except Exception as e:
            print(f"Error al enviar: {e}")


    async def _receiver(self):
        backoff = 1
        while True:
            try:
                async with websockets.connect(self.settings.server_url, ping_interval=20, ping_timeout=20) as ws:
                    self.ws = ws
                    backoff = 1

                    async for raw in ws:
                        try:
                            msg = json.loads(raw)
                        except json.JSONDecodeError:
                            continue

                        mtype = msg.get("type", "msg")

                        # --- reconstrucción de historial al conectar/reconectar ---
                        if mtype == "history":
                            items = msg.get("items", [])
                            self.last_msgs = []  # limpiar SOLO cuando llega history

                            for it in items:
                                sender = it.get("from", "???")
                                text   = it.get("text", "")
                                atts   = it.get("attachments", [])

                                parts = []
                                if text:
                                    parts.append(_esc(text))

                                for a in atts:
                                    if a.get("type") == "image" and a.get("data"):
                                        try:
                                            b     = base64.b64decode(a["data"])
                                            fname = a.get("name", "imagen.png")
                                            fpath = _save_temp_image(b, fname)               # guarda en %TEMP%/FastChat
                                            parts.append(_thumb_html_for_file(fpath, fname))  # miniatura clickeable
                                        except Exception:
                                            pass

                                shown_html = "<br>".join(parts) if parts else _esc(text)
                                self.last_msgs.append((sender, shown_html))
                                if len(self.last_msgs) > 50:
                                    self.last_msgs.pop(0)

                            # refrescar diálogo si está abierto
                            if self.reply_dialog and self.reply_dialog.isVisible():
                                self.reply_dialog.set_history(self.last_msgs)
                            continue  # no toasts para history

                        # --- mensajes en vivo ---
                        if msg.get("type") == "image" or msg.get("attachments"):
                            sender = msg.get("from", "???")
                            parts = []
                            txt = msg.get("text", "")
                            if txt:
                                parts.append(_esc(txt))

                            for a in msg.get("attachments", []):
                                if a.get("type") == "image" and a.get("data"):
                                    try:
                                        b     = base64.b64decode(a["data"])
                                        fname = a.get("name", "imagen.png")
                                        fpath = _save_temp_image(b, fname)
                                        parts.append(_thumb_html_for_file(fpath, fname))
                                    except Exception:
                                        pass

                            shown_html = "<br>".join(parts) if parts else _esc("📷 imagen")
                            self.last_msgs.append((sender, shown_html))
                            self.message_received.emit(sender, "[imagen]")  # sonido/toast
                        else:
                            sender = msg.get("from", "???")
                            text = msg.get("text", "")
                            self.last_msgs.append((sender, _esc(text)))
                            self.message_received.emit(sender, text)

            except Exception:
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 10)



    async def _force_reconnect(self):
        try:
            if self.ws and self.ws.open:
                await self.ws.close(code=1000, reason="config change")
        except Exception:
            pass
        # El loop _receiver volverá a conectar con la nueva URL
    
    async def _send_ws_payload(self, payload: dict):
        if self.ws:
            await self.ws.send(json.dumps(payload, ensure_ascii=False))


    def _ws_thread_main(self):
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        self.ws = None
        self.loop.create_task(self._receiver())
        self.loop.run_forever()

# ---------- Main ----------
def main():
    app = QtWidgets.QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    settings = Settings()
    tray = TrayClient(app, settings)
    sys.exit(app.exec())

if __name__ == "__main__":
    main()