# Services — não importar aqui para evitar inicialização antecipada de Settings.
# tasks.py já usa import local dentro da função (evita circular e eager load).
# Testes de unit conseguem importar módulos individuais sem precisar de .env.

__all__ = ["scout_service", "market_service", "finance_service", "strategy_service"]
