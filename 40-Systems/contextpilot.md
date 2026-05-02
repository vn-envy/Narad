---
type: system
status: active
created: $(date +%Y-%m-%d)
tags: [meta, infrastructure]
---

# ContextPilot — Personal Wiki System

## Architecture
- **Knowledge Layer**: Obsidian vault (this), git-backed
- **Execution Layer**: Hermes Agent (multi-LLM runtime)  
- **Routing Layer**: OpenRouter (model gateway, free tier)
- **Search Layer**: ripgrep + fzf (local), QMD (semantic, optional)
- **Sync**: Git push to private remote

## Profiles
| Profile | Provider | Model | Use Case |
|---------|----------|-------|----------|
| free | OpenRouter | qwen/qwen3.6-plus:free | Non-sensitive ideation |
| personal | OpenRouter/Direct | varies | Personal projects |
| work | Direct keys | varies | Work-scoped tasks |
| private | Non-free only | varies | Sensitive material |

## Vault Taxonomy
| Folder | Purpose | Template |
|--------|---------|----------|
| 00-Inbox | Unsorted capture | none |
| 10-Projects | Active project briefs | project.md |
| 20-Entities | People, tools, services | entity.md |
| 30-Decisions | Formal decision records | decision.md |
| 40-Systems | Infrastructure & config | system.md |
| 50-Prompts | Reusable prompt library | prompt.md |
| 60-Sessions | Daily session logs | session-log.md |
| 90-Archive | Completed/paused items | none |

## Maintenance
- Inbox zero: weekly
- Stale note audit: monthly
- Template review: quarterly
