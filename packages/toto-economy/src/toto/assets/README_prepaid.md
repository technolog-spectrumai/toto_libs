# Prepaid Accounts

Every authenticated user automatically gets one personal prepaid `LedgerAccount`
that serves as their balance for all tariff-metered actions.

**Convention:** `code = "user-prepaid-<user.pk>"`  
**Type:** `AccountType.USER`  
**Created:** lazily on first use + eagerly via `post_save(User)` signal

## API

```python
from toto.assets.prepaid import get_or_create_prepaid_account, get_prepaid_account

account, created = get_or_create_prepaid_account(user)  # idempotent
account = get_prepaid_account(user)                      # returns None if missing
```

## Backfill existing users

```bash
python manage.py backfill_prepaid_accounts
```
