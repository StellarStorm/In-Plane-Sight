import shutil
import tempfile
from pathlib import Path
from threading import Event, Lock, Thread
from typing import TYPE_CHECKING

from sources.logging_config import get_logger
from sources.swift_routes import RouteIndex, parse_fixm_message

if TYPE_CHECKING:
    from config import SwiftConfig


class SwiftSubscriber:
    def __init__(
        self,
        config: 'SwiftConfig',
        route_index: RouteIndex,
        log_level: str = 'INFO',
    ):
        self._config = config
        self._route_index = route_index
        self._stop_event = Event()
        self._connection_lock = Lock()
        self._messaging_service = None
        self._receiver = None
        self._default_trust_store: tempfile.TemporaryDirectory | None = None
        self._thread = Thread(
            target=self._run,
            name='swift-flight-fixm-consumer',
            daemon=True,
        )

        self._logger = get_logger(__name__, log_level)

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
                self._logger.debug(
                    'Error terminating SWIFT receiver',
                    exc_info=True,
                )
        if messaging_service is not None:
            try:
                messaging_service.disconnect()
            except Exception:
                self._logger.debug(
                    'Error disconnecting SWIFT messaging service',
                    exc_info=True,
                )
        self._thread.join(timeout=5)
        if self._default_trust_store is not None:
            self._default_trust_store.cleanup()

    def _trust_store_dir(self) -> str:
        """Folder of trusted root certificates for the broker's TLS check.

        Solace needs a folder, not a file. It loads every PEM certificate in
        every file in that folder (no `openssl rehash` needed). Unless the
        user configured their own, copy certifi's Mozilla root bundle into a
        private temporary folder, which works the same on every platform.

        Called before every connection attempt. Operating systems delete old
        files from temporary folders, so the copy is made again if it has
        gone missing while the app was running.
        """
        if self._config.ca_dir is not None:
            return str(self._config.ca_dir)
        store = self._default_trust_store
        if store is None or not (Path(store.name) / 'cacert.pem').is_file():
            import certifi

            if store is not None:
                self._logger.info('Re-creating the deleted SWIFT trust store')
                store.cleanup()
            store = tempfile.TemporaryDirectory(
                prefix='in-plane-sight-ca-', ignore_cleanup_errors=True
            )
            shutil.copyfile(certifi.where(), Path(store.name) / 'cacert.pem')
            self._default_trust_store = store
        return store.name

    def _log_certificate_hint(self) -> None:
        if self._config.ca_dir is not None:
            self._logger.error(
                "The SWIFT broker's TLS certificate is not trusted by the "
                'certificates in ca_dir (%s). Add the root certificate that '
                "signs the broker's certificate to that folder, or remove "
                'ca_dir from [swift] to use the built-in roots.',
                self._config.ca_dir,
            )
        else:
            self._logger.error(
                "The SWIFT broker's TLS certificate is not trusted by the "
                'built-in roots. If your network inspects TLS traffic, set '
                "ca_dir in [swift] to a folder holding your network's root "
                'certificate (PEM format).'
            )

    def _run(self) -> None:
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
                            transport_layer_properties.HOST: self._config.host,
                            service_properties.VPN_NAME: self._config.vpn,
                        }
                    )
                    .with_authentication_strategy(
                        BasicUserNamePassword.of(
                            self._config.username,
                            self._config.password.get_secret_value(),
                        )
                    )
                    .with_transport_security_strategy(
                        TLS.create().with_certificate_validation(
                            False,
                            True,
                            self._trust_store_dir(),
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

                receiver = messaging_service.create_persistent_message_receiver_builder().build(
                    Queue.durable_exclusive_queue(self._config.queue)
                )
                receiver.start()
                with self._connection_lock:
                    self._receiver = receiver
                self._logger.info(
                    f'Listening to SWIFT queue {self._config.queue}'
                )

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
                        self._logger.exception(
                            'Unable to process a SWIFT Flight FIXM message'
                        )
                    finally:
                        receiver.ack(message)
            except Exception as error:
                if not self._stop_event.is_set():
                    self._logger.exception(
                        'SWIFT subscriber disconnected; retrying'
                    )
                    if 'certificate' in str(error).lower():
                        self._log_certificate_hint()
            finally:
                with self._connection_lock:
                    self._receiver = None
                    self._messaging_service = None
                if receiver is not None:
                    try:
                        receiver.terminate()
                    except Exception:
                        self._logger.debug(
                            'Error terminating SWIFT receiver', exc_info=True
                        )
                if messaging_service is not None:
                    try:
                        messaging_service.disconnect()
                    except Exception:
                        self._logger.debug(
                            'Error disconnecting SWIFT messaging service',
                            exc_info=True,
                        )
            self._stop_event.wait(5)
