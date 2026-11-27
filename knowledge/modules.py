"""Readable names for the PrismERP modules, as the docs home page shows them (6K-2)."""

MODULE_NAMES = {
    "financial": "Financial Management System",
    "supply-chain": "Supply Chain Management",
    "sales": "Sales & Distribution System",
    "human-capital": "Human Capital Management",
    "production": "Production Planning & Control",
    "customer-relationship": "Customer Relationship Management",
    "productivity": "Productivity Management",
    "admin-logistics": "Admin & Logistics",
    "project": "Project System",
    "core-system": "Core System Components",
}


def module_name(slug):
    """The docs' name for a module; for one not in the list, built from the slug."""
    return MODULE_NAMES.get(slug, slug.replace("-", " ").title())
