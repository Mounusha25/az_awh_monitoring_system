# Code_Original — Historical Reference Only

This folder contains the **original version** of the AquaPars monitoring system as it was first built and deployed.

**⚠️ Do NOT run or deploy these files.** The live, active code is in `RPi_USB_Package/`.

## Why It's Kept
- Shows the starting point of the system before refactoring
- Useful for understanding what changed and why during development
- Reference for anyone new to the project who wants to understand the evolution

## Key Differences from Live Code

| Original File | Current Replacement | What Changed |
|---------------|---------------------|--------------|
| `ui_display.py` | `awh_ui_layout.py` | UI redesign and improved layout |
| `read_env_anemometer.py` | `intake_anemometer.py` + `outtake_anemometer.py` | Split into separate intake/outtake sensors |
| `send_mail.py` | (removed) | Email alerting was deprecated |
| `test_*.py` | `test_system/` | One-time hardware validation scripts moved to organized test folder |

## When to Reference This

- If you need to understand why a specific architectural decision was made
- If you're tracing the history of a feature back to its original implementation
- If you're writing documentation about the system's evolution

## When NOT to Use This

- Never deploy these files to production Raspberry Pi stations
- Don't copy code from here without checking if it's outdated
- Don't use these as examples for new features (use `RPi_USB_Package/` instead)

---

*Last updated: 2026-06-19*
