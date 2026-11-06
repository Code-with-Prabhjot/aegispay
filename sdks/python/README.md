# AegisPay Python SDK
```python
from aegispay import AegisPayClient

client = AegisPayClient(base_url="http://localhost:8000")
contract = client.propose_sla("buyer-1", "worker-1", "w@pay.com", 15_000_000, "Build rate limiter")
client.lock_escrow(contract["agreement_id"])
client.submit_code(contract["agreement_id"], "def process(): return True")
proof = client.get_merkle_proof(contract["agreement_id"], leaf_index=0)
```