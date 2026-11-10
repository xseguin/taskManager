"""Point d'entrée : python -m assistant.main"""
from __future__ import annotations

import logging
import os
from datetime import datetime


def main() -> None:
    import anthropic
    import groq
    from dotenv import load_dotenv

    from assistant.agent.agent import Agent
    from assistant.bot import build_application
    from assistant.config import load_config
    from assistant.controller import Controller
    from assistant.conversation_store import ConversationStore
    from assistant.db import connect
    from assistant.executor import Executor
    from assistant.pending_store import PendingStore
    from assistant.todoist_client import TodoistClient
    from assistant.transcriber import GroqTranscriber

    load_dotenv()
    config = load_config(os.environ)
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)  # n'affiche pas le jeton dans les URL

    def clock() -> datetime:
        return datetime.now(config.timezone)

    conn = connect(config.db_path)
    store = PendingStore(conn, clock)
    todoist = TodoistClient(config.todoist_token)
    agent = Agent(anthropic.Anthropic(api_key=config.anthropic_api_key, max_retries=2), todoist,
                  ConversationStore(conn, clock), config.claude_model, clock, tz=config.timezone)
    controller = Controller(config.allowed_user_id,
                            GroqTranscriber(groq.Groq(api_key=config.groq_api_key)),
                            agent, store, Executor(todoist, store))
    app = build_application(config, controller, store)
    logging.getLogger(__name__).info("bot démarré (modèle %s)", config.claude_model)
    app.run_polling()


if __name__ == "__main__":
    main()
