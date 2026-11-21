"""Storage configuration shared by package import tests."""
from asaree_workspace_core.storage import NAMESPACES

from asaree.config import AsareeSettings


def storage_settings(root):
    # Child-process configuration exports explicit namespace paths into the
    # environment. Reset those too: data_root alone cannot isolate a test.
    return AsareeSettings(_env_file=None, data_root=str(root),
                          **{field: '' for field, _ in NAMESPACES.values()})
