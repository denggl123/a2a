"""Compatibility entry for the current installed four-node payment acceptance.

Uses an actual HTTP Agent, mandatory free samples and all three payment methods.
Policies are restored by the shared acceptance entry; no empty-wallet assumptions.
"""
from automatic_settlement_acceptance import main

if __name__ == "__main__":
    main()
