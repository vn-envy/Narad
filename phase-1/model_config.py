"""
Central model assignments for Phase 1.
Override any model via environment variables.

DeepSeek V4 model IDs (api.deepseek.com):
  Pro:   deepseek-v4-pro
  Flash: deepseek-v4-flash
"""
import os

DS_PRO   = os.environ.get("DS_PRO_MODEL",   "deepseek/deepseek-v4-pro")
DS_FLASH = os.environ.get("DS_FLASH_MODEL", "deepseek/deepseek-v4-flash")
GPT4O    = os.environ.get("NARAD_MODEL",    "openai/gpt-4o")

AVATAR_MODELS = {
    "narad":       GPT4O,      # routing accuracy — non-negotiable
    "matsya":      DS_FLASH,   # search synthesis, tool-light
    "varaha":      GPT4O,      # long-doc, potentially sensitive content
    "narasimha":   DS_PRO,     # code reasoning + root cause
    "rama":        DS_FLASH,   # structured output, cheap + sufficient
    "krishna":     DS_FLASH,   # prose drafting, cheap + sufficient
    "buddha":      DS_PRO,     # adversarial reasoning, analytical depth
    "parashurama": DS_PRO,     # code — DeepSeek best-in-class
}
