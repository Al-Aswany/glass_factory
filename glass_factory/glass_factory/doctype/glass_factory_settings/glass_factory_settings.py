from frappe.model.document import Document

from glass_factory.glass_factory.settings_validation import validate_settings_document


class GlassFactorySettings(Document):
	def validate(self):
		validate_settings_document(self)
