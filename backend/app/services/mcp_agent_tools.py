"""
backend/app/services/mcp_agent_tools.py
Standard JSON-RPC 2.0 Model Context Protocol (MCP) tool schemas and definitions for AegisPay.
"""

from typing import Any, Dict, List

MCP_JSONRPC_TOOLS: List[Dict[str, Any]] = [
    {
        "name": "propose_contract",
        "description": "Autonomous buyer agent tool to propose an SLA contract and lock escrow funds via PayPal authorization hold.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "buyer_agent_id": {
                    "type": "string",
                    "description": "Unique identifier of the buyer agent initiating the task",
                },
                "worker_agent_id": {
                    "type": "string",
                    "description": "Unique identifier of the worker agent hired to solve the task",
                },
                "worker_paypal_email": {
                    "type": "string",
                    "format": "email",
                    "description": "PayPal receiver email for final settlement payout",
                },
                "budget_micros": {
                    "type": "integer",
                    "minimum": 1000000,
                    "description": "Agreed escrow budget in integer micro-dollars ($1.00 USD = 1,000,000 micros)",
                },
                "task_description": {
                    "type": "string",
                    "description": "Detailed task description and SLA specifications",
                },
                "timeout_seconds": {
                    "type": "integer",
                    "default": 3600,
                    "description": "Execution TTL in seconds before contract authorization expires",
                },
                "vault_id": {
                    "type": "string",
                    "description": "Optional PayPal Vault payment method token for autonomous pre-authorized billing",
                },
                "test_suite": {
                    "type": "string",
                    "description": "Buyer acceptance test suite committed at creation for verification grading",
                },
            },
            "required": [
                "buyer_agent_id",
                "worker_agent_id",
                "worker_paypal_email",
                "budget_micros",
                "task_description",
            ],
        },
    },
    {
        "name": "submit_deliverable",
        "description": "Autonomous worker agent tool to submit completed code deliverable to the air-gapped verification arbiter.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "agreement_id": {
                    "type": "string",
                    "description": "The SLA agreement identifier under which deliverable is submitted",
                },
                "source_code": {
                    "type": "string",
                    "description": "Worker Python source code deliverable",
                },
                "test_suite": {
                    "type": "string",
                    "description": "Optional test suite override in mock mode",
                },
                "runtime": {
                    "type": "string",
                    "description": "Optional runtime target (e.g. python3.11)",
                },
            },
            "required": [
                "agreement_id",
                "source_code",
            ],
        },
    },
    {
        "name": "verify_audit_proof",
        "description": "Autonomous agent tool to cryptographically verify settlement non-repudiation using 7-leaf Merkle tree proof.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "agreement_id": {
                    "type": "string",
                    "description": "Unique SLA contract agreement identifier to verify",
                },
                "leaf_index": {
                    "type": "integer",
                    "minimum": 0,
                    "maximum": 6,
                    "default": 0,
                    "description": "Merkle leaf index to verify (0: Contract, 1: Hold, 2: Code, 3: AST, 4: Tests, 5: Settlement, 6: Payout)",
                },
            },
            "required": [
                "agreement_id",
            ],
        },
    },
]
