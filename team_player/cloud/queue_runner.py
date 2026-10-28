"""Private Container App queue consumer. Uses the unchanged generation worker."""
import logging
import threading
import time
from azure.identity import DefaultAzureCredential
from azure.storage.queue import QueueClient, TextBase64DecodePolicy
from team_player.cloud.generation_store import GenerationStore, QUEUE, ID
from team_player.cloud.generation_worker import process, renewing_lease
import os


def consume(queue, message, store):
    job = message.content
    receipt = [message.pop_receipt]
    stop = threading.Event()
    errors = []
    def renew():
        while not stop.wait(20):
            try:
                updated = queue.update_message(message.id, pop_receipt=receipt[0], visibility_timeout=60)
                receipt[0] = updated.pop_receipt
            except Exception as exc:
                errors.append(exc)
                logging.exception('Queue visibility renewal failed')
                return
    thread = threading.Thread(target=renew, daemon=True)
    thread.start()
    done = False
    try:
        if not ID.fullmatch(job):
            logging.error('Discarding invalid job identifier')
            done = True
        elif message.dequeue_count > 5:
            with renewing_lease(store.blob(job)) as (lease, lease_errors):
                doc = store.get(job)
                if doc.get('status') != 'ready':
                    store.put({**doc, 'status':'failed', 'error':'Generation failed after retries. Please submit again.'}, lease)
            done = True
        else:
            process(job, store)
            done = True
    finally:
        stop.set()
        thread.join(timeout=35)
        if done and not errors and not thread.is_alive():
            queue.delete_message(message.id, receipt[0])


def main():
    logging.basicConfig(level=logging.INFO)
    credential = DefaultAzureCredential()
    queue = QueueClient(os.environ['TEAM_PLAYER_BLOB_ACCOUNT_URL'].replace('.blob.', '.queue.'),
                        QUEUE, credential=credential, message_decode_policy=TextBase64DecodePolicy())
    store = GenerationStore()
    logging.info('Generation worker ready; season 2026-27; concurrency 1')
    while True:
        try:
            message = next(iter(queue.receive_messages(messages_per_page=1, visibility_timeout=60)), None)
            if message is None:
                time.sleep(3)
            else:
                consume(queue, message, store)
        except Exception:
            logging.exception('Queue processing failed; retrying')
            time.sleep(5)

if __name__ == '__main__':
    main()
