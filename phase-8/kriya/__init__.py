"""Kriya: the task runtime for errands that take many steps.

An avatar starts a task with ``start_task`` and gets its id at once; the task
then runs its own perceive -> act -> settle -> verify loop in the background
with a dedicated operator model, outside the avatar's context. Tasks are
durable (SQLite per profile), cancellable from the phone within one step,
resumable after a server restart, and watched through a live frame.

Modules:
  store       per-profile task store and event log
  perception  viewport-scoped accessibility observations (about 2K tokens)
  browser     the isolated and cloud browser surfaces (act, settle, frames)
  desktop     a window of the host Mac through the persistent cua-driver session
  phone       Android tasks that Artemis runs, admitted, approved and followed here
  operator    the operator protocol: prompt, JSON actions, model and scripted
  runtime     queue, surface locks, the loop, approvals, help, delivery
  tool        ``start_task``, the avatars' entry point
  api         ``/tasks`` routes (profile-scoped)
"""
