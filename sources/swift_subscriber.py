import logging
from threading import Event, Lock, Thread
from typing import TYPE_CHECKING

from sources.swift_routes import RouteIndex, parse_fixm_message

if TYPE_CHECKING:
    from config import Config


class SwiftSubscriber:
    def __init__(self, config: 'Config', route_index: RouteIndex):
        self._config = config
        self._route_index = route_index
        self._stop_event = Event()
        self._connection_lock = Lock()
        self._messaging_service = None
        self._receiver = None
        self._thread = Thread(
            target=self._run,
            name='swift-flight-fixm-consumer',
            daemon=True,
        )

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        with self._connection_lock:
            receiver = self._receiver
            messaging_service = self._messaging_service
        if receiver is not None:
            try:
                receiver.terminate()
            except Exception:
                logging.getLogger(__name__).debug(
                    'Error terminating SWIFT receiver',
                    exc_info=True,
                )
        if messaging_service is not None:
            try:
                messaging_service.disconnect()
            except Exception:
                logging.getLogger(__name__).debug(
                    'Error disconnecting SWIFT messaging service',
                    exc_info=True,
                )
        self._thread.join(timeout=5)

    def _run(self) -> None:
        logger = logging.getLogger(__name__)
        while not self._stop_event.is_set():
            self._route_index.expire()
            messaging_service = None
            receiver = None
            try:
                from solace.messaging.config.authentication_strategy import (
                    BasicUserNamePassword,
                )
                from solace.messaging.config.retry_strategy import RetryStrategy
                from solace.messaging.config.solace_properties import (
                    service_properties,
                    transport_layer_properties,
                )
                from solace.messaging.config.transport_security_strategy import (
                    TLS,
                )
                from solace.messaging.messaging_service import MessagingService
                from solace.messaging.resources.queue import Queue

                messaging_service = (
                    MessagingService.builder()
                    .from_properties(
                        {
                            transport_layer_properties.HOST: self._config.swift_host,
                            service_properties.VPN_NAME: self._config.swift_vpn,
                        }
                    )
                    .with_authentication_strategy(
                        BasicUserNamePassword.of(
                            self._config.swift_username,
                            self._config.swift_password,
                        )
                    )
                    .with_transport_security_strategy(
                        TLS.create().with_certificate_validation(
                            False,
                            True,
                            self._config.swift_ca_dir,
                        )
                    )
                    .with_connection_retry_strategy(
                        RetryStrategy.parametrized_retry(1, 1000)
                    )
                    .with_reconnection_retry_strategy(
                        RetryStrategy.parametrized_retry(1, 1000)
                    )
                    .build()
                )
                with self._connection_lock:
                    self._messaging_service = messaging_service
                messaging_service.connect()

                receiver = (
                    messaging_service.create_persistent_message_receiver_builder()
                    .build(Queue.durable_exclusive_queue(self._config.swift_queue))
                )
                receiver.start()
                with self._connection_lock:
                    self._receiver = receiver
                logger.info('Listening to SWIFT queue %s', self._config.swift_queue)

                while not self._stop_event.is_set():
                    message = receiver.receive_message(timeout=1000)
                    self._route_index.expire()
                    if message is None:
                        continue
                    try:
                        payload = message.get_payload_as_string()
                        if payload is None:
                            payload = message.get_payload_as_bytes()
                        updates = parse_fixm_message(
                            payload,
                            message.get_properties() or {},
                        )
                        for update in updates:
                            self._route_index.apply(update)
                    except Exception:
                        logger.exception('Unable to process a SWIFT Flight FIXM message')
                    finally:
                        receiver.ack(message)
            except Exception:
                if not self._stop_event.is_set():
                    logger.exception('SWIFT subscriber disconnected; retrying')
            finally:
                with self._connection_lock:
                    self._receiver = None
                    self._messaging_service = None
                if receiver is not None:
                    try:
                        receiver.terminate()
                    except Exception:
                        logger.debug('Error terminating SWIFT receiver', exc_info=True)
                if messaging_service is not None:
                    try:
                        messaging_service.disconnect()
                    except Exception:
                        logger.debug(
                            'Error disconnecting SWIFT messaging service',
                            exc_info=True,
                        )
            self._stop_event.wait(5)
