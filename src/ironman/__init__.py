"""
Ironman / Race Tracker — gera criativos automáticos pra milestones de provas.

Milestones T-30, T-15, T-7, T-1 (countdown) + T+1 (resultados, só `kind=ironman`).
Roda 1x/dia, dispara approvals via Telegram (mesmo canal dos criativos normais,
prefixo `[Ironman · T-N]` na caption pra distinção visual).

Provas são lidas de `config/races.yml`.
"""
