"""Import built-in modules to register their tools with the action registry."""

from importlib import import_module

for _module_name in (
	"generate_report",
	"list_anomalies",
	"list_suppliers",
	"supplier_detail",
):
	import_module(f"{__name__}.{_module_name}")