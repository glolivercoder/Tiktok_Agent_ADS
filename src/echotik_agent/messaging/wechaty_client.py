"""Cliente Wechaty para envio de mensagens em massa (com mock para testes)."""
from __future__ import annotations

import asyncio
import logging
import random
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any, AsyncGenerator, Optional

import pytz

# Tenta importar wechaty, mas permite mock para testes
try:
    from wechaty import Wechaty, Contact as WechatyContact, FileBox
    WECHATY_AVAILABLE = True
except ImportError:
    WECHATY_AVAILABLE = False
    Wechaty = None
    WechatyContact = None
    FileBox = None

from .models import WechatyConfig, Contact, MessageQueueItem

logger = logging.getLogger(__name__)


class WechatySendError(Exception):
    """Erro no envio via Wechaty."""
    def __init__(self, message: str, contact_id: str, original_error: Exception | None = None):
        super().__init__(message)
        self.contact_id = contact_id
        self.original_error = original_error


class MockWechatyContact:
    """Mock do contato Wechaty para testes."""
    def __init__(self, contact_id: str, name: str = "Test Contact"):
        self.contact_id = contact_id
        self.name = name
        self.alias = ""
        self.weixin = ""
        self.city = ""
        self.province = ""
        self.gender = 0

    async def ready(self):
        pass

    async def say(self, message):
        class MockMessage:
            message_id = "mock_msg_id"
        return MockMessage()


class MockWechaty:
    """Mock do Wechaty para testes sem dependência real."""
    def __init__(self, config):
        self.config = config
        self._contacts = {}
        self._ready = False
        
    def options(self, name=None, puppet=None, puppet_options=None):
        return self
    
    def on(self, event, handler):
        pass
    
    async def start(self):
        self._ready = True
        
    async def stop(self):
        pass
    
    def Contact(self):
        class ContactLoader:
            def __init__(self, parent):
                self.parent = parent
            
            def load(self, contact_id):
                if contact_id not in self.parent._contacts:
                    self.parent._contacts[contact_id] = MockWechatyContact(contact_id)
                return self.parent._contacts[contact_id]
            
            async def find_all(self, query):
                return []
        return ContactLoader(self)
    
    @property
    def ready(self):
        return self._ready


class WechatyClient:
    """Wrapper assíncrono para Wechaty com envio controlado (suporta mock para testes)."""

    def __init__(self, config: WechatyConfig) -> None:
        self.config = config
        self._bot: Optional[Any] = None
        self._ready_event = asyncio.Event()
        self._running = False
        self._use_mock = not WECHATY_AVAILABLE

    @property
    def is_ready(self) -> bool:
        return self._ready_event.is_set() and self._bot is not None

    @property
    def bot(self) -> Optional[Any]:
        return self._bot

    async def start(self) -> None:
        """Inicializa e conecta o Wechaty (ou mock para testes)."""
        if self._running:
            logger.warning("WechatyClient já está rodando")
            return

        self._running = True
        logger.info("Iniciando Wechaty...")

        try:
            if self._use_mock:
                logger.info("Usando Wechaty MOCK para testes")
                self._bot = MockWechaty(self.config)
                await self._bot.start()
                self._ready_event.set()
                logger.info("Wechaty MOCK pronto para uso")
            else:
                self._bot = Wechaty().options(
                    name=self.config.name,
                    puppet=self.config.puppet,
                    puppet_options={
                        "token": self.config.puppet_token
                    } if self.config.puppet_token else {}
                )

                self._bot.on("login", self._on_login)
                self._bot.on("logout", self._on_logout)
                self._bot.on("scan", self._on_scan)
                self._bot.on("error", self._on_error)
                self._bot.on("ready", self._on_ready)

                await self._bot.start()
                logger.info("Wechaty iniciado, aguardando login...")

                # Aguarda ready com timeout
                try:
                    await asyncio.wait_for(self._ready_event.wait(), timeout=300)
                    logger.info("Wechaty pronto para uso")
                except asyncio.TimeoutError:
                    logger.warning("Timeout aguardando Wechaty ready - continuando mesmo assim")
                    self._ready_event.set()

        except Exception as e:
            logger.exception("Falha ao iniciar Wechaty")
            self._running = False
            raise

    async def stop(self) -> None:
        """Para o Wechaty graciosamente."""
        if self._bot and self._running:
            logger.info("Parando Wechaty...")
            self._running = False
            await self._bot.stop()
            logger.info("Wechaty parado")

    def _on_login(self, contact: Any) -> None:
        logger.info(f"Wechaty logado como: {contact.name}")

    def _on_logout(self, contact: Any) -> None:
        logger.warning(f"Wechaty deslogado: {contact.name}")
        self._ready_event.clear()

    def _on_scan(self, status: str, qr_code: str, data: str) -> None:
        logger.info(f"QR Code para login: {qr_code[:50]}...")

    def _on_error(self, error: Exception) -> None:
        logger.error(f"Wechaty erro: {error}")

    def _on_ready(self) -> None:
        logger.info("Wechaty pronto (ready)")
        self._ready_event.set()

    def is_business_hours(self, now: Optional[datetime] = None) -> bool:
        """Verifica se está dentro do horário comercial."""
        if now is None:
            now = datetime.now(pytz.timezone(self.config.timezone))
        hour = now.hour
        return self.config.business_hours_start <= hour < self.config.business_hours_end

    def get_random_delay(self) -> int:
        """Gera delay aleatório entre min e max."""
        return random.randint(
            self.config.min_delay_seconds,
            self.config.max_delay_seconds
        )

    @asynccontextmanager
    async def _send_context(self, contact_id: str) -> AsyncGenerator[Any, None]:
        """Context manager para obter contato e enviar mensagem."""
        if not self._bot:
            raise WechatySendError("Wechaty não inicializado", contact_id)

        try:
            if self._use_mock:
                contact = self._bot.Contact().load(contact_id)
            else:
                contact = self._bot.Contact.load(contact_id)
            
            if not contact:
                raise WechatySendError(f"Contato não encontrado: {contact_id}", contact_id)
            yield contact
        except Exception as e:
            raise WechatySendError(f"Erro ao carregar contato {contact_id}: {e}", contact_id) from e

    async def send_text(self, contact_id: str, message: str) -> dict[str, Any]:
        """Envia mensagem de texto para um contato."""
        if not self.is_ready:
            raise WechatySendError("Wechaty não está pronto", contact_id)

        async with self._send_context(contact_id) as contact:
            try:
                logger.info(f"Enviando para {contact_id}: {message[:50]}...")
                msg = await contact.say(message)
                logger.info(f"Enviado para {contact_id}: msg_id={msg.message_id}")
                return {
                    "success": True,
                    "message_id": msg.message_id,
                    "timestamp": datetime.utcnow().isoformat(),
                }
            except Exception as e:
                logger.error(f"Erro ao enviar para {contact_id}: {e}")
                raise WechatySendError(f"Falha no envio: {e}", contact_id) from e

    async def send_file(self, contact_id: str, file_path: str) -> dict[str, Any]:
        """Envia arquivo para um contato."""
        if not self.is_ready:
            raise WechatySendError("Wechaty não está pronto", contact_id)

        async with self._send_context(contact_id) as contact:
            try:
                if self._use_mock:
                    # Mock não suporta arquivos reais
                    logger.info(f"MOCK: Enviando arquivo {file_path} para {contact_id}")
                    return {"success": True, "mock": True}
                else:
                    from wechaty import FileBox
                    file_box = FileBox.from_file(file_path)
                    await contact.say(file_box)
                    return {"success": True}
            except Exception as e:
                logger.error(f"Erro ao enviar arquivo para {contact_id}: {e}")
                raise WechatySendError(f"Falha no envio de arquivo: {e}", contact_id) from e

    async def send_image(self, contact_id: str, image_url: str) -> dict[str, Any]:
        """Envia imagem via URL."""
        if not self.is_ready:
            raise WechatySendError("Wechaty não está pronto", contact_id)

        async with self._send_context(contact_id) as contact:
            try:
                if self._use_mock:
                    logger.info(f"MOCK: Enviando imagem {image_url} para {contact_id}")
                    return {"success": True, "mock": True}
                else:
                    from wechaty import FileBox
                    file_box = FileBox.from_url(image_url)
                    await contact.say(file_box)
                    return {"success": True}
            except Exception as e:
                logger.error(f"Erro ao enviar imagem para {contact_id}: {e}")
                raise WechatySendError(f"Falha no envio de imagem: {e}", contact_id) from e

    async def get_contact(self, contact_id: str) -> Optional[dict]:
        """Obtém informações do contato."""
        if not self._bot:
            return None

        try:
            contact = self._bot.Contact.load(contact_id)
            if not contact:
                return None
            await contact.ready()
            return {
                "id": contact.contact_id,
                "name": contact.name,
                "alias": contact.alias,
                "weixin": contact.weixin,
                "city": contact.city,
                "province": contact.province,
                "gender": contact.gender,
            }
        except Exception:
            return None

    async def search_contacts(self, query: str) -> list[dict]:
        """Busca contatos por nome/alias."""
        if not self._bot:
            return []

        try:
            contacts = await self._bot.Contact.find_all(query)
            results = []
            for c in contacts:
                await c.ready()
                results.append({
                    "id": c.contact_id,
                    "name": c.name,
                    "alias": c.alias,
                })
            return results
        except Exception:
            return []