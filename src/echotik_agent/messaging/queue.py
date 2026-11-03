"""Scheduler de fila de mensagens com delay aleatório e horário comercial."""
from __future__ import annotations

import asyncio
import logging
import random
import uuid
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Optional

import pytz

from .models import (
    MessageQueueItem,
    BatchReview,
    BatchStatus,
    MessageStatus,
    QueueConfig,
    Contact,
)

logger = logging.getLogger(__name__)


@dataclass
class QueuedMessage:
    """Mensagem na fila de envio."""
    item: MessageQueueItem
    scheduled_at: datetime
    retries: int = 0


class QueueError(Exception):
    """Erro na fila de mensagens."""
    pass


class MessageQueueScheduler:
    """Scheduler de fila com delay aleatório, horário comercial e retry."""

    def __init__(
        self,
        config: QueueConfig,
        wechaty_client: Any,  # WechatyClient
        store: Any,  # Store
    ) -> None:
        self.config = config
        self.wechaty = wechaty_client
        self.store = store
        self.timezone = pytz.timezone(config.timezone)

        # Filas
        self._pending: deque[QueuedMessage] = deque()
        self._sending: set[str] = set()
        self._dlq: deque[QueuedMessage] = deque(maxlen=config.dlq_max_size)

        # Estado
        self._running = False
        self._worker_task: asyncio.Task | None = None
        self._semaphore = asyncio.Semaphore(config.max_concurrent_sends)

        # Stats
        self.stats = {
            "total_queued": 0,
            "total_sent": 0,
            "total_failed": 0,
            "total_retries": 0,
            "dlq_size": 0,
        }

    def is_business_hours(self, now: Optional[datetime] = None) -> bool:
        """Verifica se está dentro do horário comercial."""
        if now is None:
            now = datetime.now(pytz.timezone(self.config.timezone))
        # Apenas dias úteis (seg-sex)
        if now.weekday() >= 5:  # 5=sábado, 6=domingo
            return False
        hour = now.hour
        return self.config.business_hours_start <= now.hour < self.config.business_hours_end

    def get_random_delay(self) -> int:
        """Gera delay aleatório entre min e max."""
        return random.randint(
            self.config.min_delay_seconds,
            self.config.max_delay_seconds
        )

    def enqueue_batch(self, batch: BatchReview) -> int:
        """Adiciona lote aprovado à fila de envio."""
        if batch.status != "approved":
            raise QueueError(f"Lote {batch.id} não está aprovado para envio")

        count = 0
        now = datetime.now(pytz.timezone(self.config.timezone))

        for msg in batch.generated_messages:
            if msg.id in batch.approved_ids and msg.status == "approved":
                # Calcula agendamento com delay aleatório
                delay = self.get_random_delay()
                scheduled_at = datetime.now(pytz.timezone(self.config.timezone))
                # Se fora do horário comercial, agenda para próximo horário comercial
                if not self.is_business_hours():
                    # Agenda para próximo dia útil às 9h
                    next_day = datetime.now(pytz.timezone(self.config.timezone)).replace(
                        hour=9, minute=0, second=0, microsecond=0
                    )
                    # Se hoje já passou das 18h, agenda para amanhã
                    if datetime.now(pytz.timezone(self.config.timezone)).hour >= 18:
                        next_day = next_day.replace(day=next_day.day + 1)
                        # Pula fim de semana
                        while next_day.weekday() >= 5:
                            next_day = next_day.replace(day=next_day.day + 1)
                    scheduled_at = next_day
                else:
                    scheduled_at = datetime.now(pytz.timezone(self.config.timezone))

                # Adiciona delay aleatório
                delay = self.get_random_delay()
                scheduled_at = scheduled_at.replace(
                    second=0, microsecond=0
                )
                # Adiciona delay em segundos
                scheduled_at = scheduled_at + timedelta(seconds=self.get_random_delay())

                msg = msg.model_copy(update={
                    "status": "scheduled",
                    "scheduled_at": scheduled_at,
                })

                self._pending.append(QueuedMessage(
                    item=msg,
                    scheduled_at=scheduled_at,
                ))
                count += 1

        self.stats["total_queued"] += count
        logger.info(f"Enfileirados {count} mensagens do lote {batch.id}")
        return count

    async def start(self) -> None:
        """Inicia o worker de fila."""
        if self._running:
            return

        self._running = True
        self._worker_task = asyncio.create_task(self._worker_loop())
        logger.info("Queue scheduler iniciado")

    async def stop(self) -> None:
        """Para o worker graciosamente."""
        self._running = False
        if self._worker_task:
            self._worker_task.cancel()
            try:
                await self._worker_task
            except asyncio.CancelledError:
                pass
        logger.info("Queue scheduler parado")

    async def _worker_loop(self) -> None:
        """Loop principal do worker."""
        logger.info("Worker de fila iniciado")
        while self._running:
            try:
                await self._process_pending()
                await asyncio.sleep(self.config.check_interval_seconds)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.exception(f"Erro no worker loop: {e}")
                await asyncio.sleep(self.config.check_interval_seconds)

    async def _process_pending(self) -> None:
        """Processa mensagens pendentes que estão prontas para envio."""
        if not self.is_business_hours():
            return

        now = datetime.now(pytz.timezone(self.config.timezone))

        # Move mensagens prontas da pending para sending
        ready_messages = []
        still_pending = deque()

        for qmsg in self._pending:
            if qmsg.scheduled_at <= datetime.now(pytz.timezone(self.config.timezone)):
                if qmsg.item.id not in self._sending and len(self._sending) < self.config.max_concurrent_sends:
                    ready_messages.append(qmsg)
                else:
                    still_pending.append(qmsg)
            else:
                still_pending.append(qmsg)

        self._pending = still_pending

        # Envia mensagens prontas
        for qmsg in ready_messages:
            asyncio.create_task(self._send_message(qmsg))

    async def _send_message(self, qmsg: QueuedMessage) -> None:
        """Envia uma mensagem individual."""
        msg = qmsg.item
        msg_id = msg.id
        self._sending.add(msg_id)

        try:
            # Atualiza status para enviando
            msg = msg.model_copy(update={
                "status": "sending",
                "updated_at": datetime.utcnow(),
            })
            await self._update_message_status(msg)

            # Envia via Wechaty
            result = await self.wechaty.send_text(msg.contact.wechat_id, msg.current_message)

            # Sucesso
            msg = msg.model_copy(update={
                "status": "sent",
                "sent_at": datetime.utcnow(),
                "wechaty_response": result,
                "updated_at": datetime.utcnow(),
            })
            await self._update_message_status(msg)
            self.stats["total_sent"] += 1
            logger.info(f"Mensagem {msg_id} enviada com sucesso")

        except Exception as e:
            # Erro - tenta retry ou DLQ
            qmsg.retries += 1
            self.stats["total_retries"] += 1

            if qmsg.retries >= self.config.max_retries:
                # Move para DLQ
                msg = msg.model_copy(update={
                    "status": "dead_letter",
                    "last_error": str(e),
                    "attempts": qmsg.retries,
                    "updated_at": datetime.utcnow(),
                })
                await self._update_message_status(msg)
                self._dlq.append(qmsg)
                self.stats["dlq_size"] = len(self._dlq)
                self.stats["total_failed"] += 1
                logger.error(f"Mensagem {msg.id} movida para DLQ após {qmsg.retries} tentativas: {e}")
            else:
                # Reagenda com backoff exponencial
                delay = self.config.retry_base_delay_seconds * (2 ** (qmsg.retries - 1))
                new_scheduled = datetime.now(pytz.timezone(self.config.timezone)) + timedelta(seconds=delay)
                qmsg.scheduled_at = new_scheduled
                msg = msg.model_copy(update={
                    "status": "retrying",
                    "attempts": qmsg.retries,
                    "last_error": str(e),
                    "scheduled_at": new_scheduled,
                    "updated_at": datetime.utcnow(),
                })
                await self._update_message_status(msg)
                self._pending.append(qmsg)
                logger.warning(f"Mensagem {msg_id} reagendada para retry {qmsg.retries}/{self.config.max_retries} em {delay}s: {e}")

        finally:
            self._sending.discard(msg_id)

    async def _update_message_status(self, msg: MessageQueueItem) -> None:
        """Atualiza status da mensagem no store."""
        try:
            # O store precisa ter um método para atualizar mensagens na fila
            # Por enquanto apenas log
            logger.debug(f"Status atualizado: {msg.id} -> {msg.status}")
        except Exception as e:
            logger.warning(f"Falha ao atualizar status: {e}")

    def get_stats(self) -> dict[str, Any]:
        """Retorna estatísticas da fila."""
        return {
            **self.stats,
            "pending": len(self._pending),
            "sending": len(self._sending),
            "dlq_size": len(self._dlq),
            "is_business_hours": self.is_business_hours(),
        }

    def get_dlq_messages(self) -> list[QueuedMessage]:
        """Retorna mensagens na DLQ."""
        return list(self._dlq)

    async def retry_dlq(self, msg_id: str) -> bool:
        """Reprocessa mensagem da DLQ."""
        for i, qmsg in enumerate(self._dlq):
            if qmsg.item.id == msg_id:
                qmsg.retries = 0
                qmsg.scheduled_at = datetime.now(pytz.timezone(self.config.timezone))
                self._dlq.remove(qmsg)
                self._pending.append(qmsg)
                self.stats["dlq_size"] = len(self._dlq)
                logger.info(f"Mensagem {msg_id} movida da DLQ para fila")
                return True
        return False

    def get_pending_count(self) -> int:
        return len(self._pending)

    def get_sending_count(self) -> int:
        return len(self._sending)