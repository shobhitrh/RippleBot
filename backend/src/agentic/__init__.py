"""
Agentic orchestration layer (PIA × RippleBot unification — see PRD §18).

This package is ADDITIVE and OFF BY DEFAULT (config.AGENTIC_MODE). It transplants
PIA's Claude tool-use loop on top of RippleBot's existing retrieval substrate,
exposing every knowledge source — RippleBot's own tiers AND PIA's live-DB / help
-center tools — as callable tools the Claude loop selects between.

Design rules (why nothing here can break existing RippleBot):
  * The existing /api/chat/query pipeline is never imported or modified.
  * Every heavy dependency (anthropic, pymysql, httpx) is imported lazily inside
    functions, so a missing package never crashes app startup.
  * PIA tools whose credentials aren't set return a clear "not configured" string
    instead of raising — plug the keys into .env and they light up (PRD §19.8).
"""
