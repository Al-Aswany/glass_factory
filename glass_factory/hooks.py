app_name = "glass_factory"
app_title = "Glass Factory"
app_publisher = "Mahmoud Hussein"
app_description = "app for Glass Factory"
app_email = "mahmudhussain2001ab@gmail.com"
app_license = "mit"

# Apps
# ------------------

# required_apps = []

# Each item in the list will be shown as an app in the apps page
# add_to_apps_screen = [
# 	{
# 		"name": "glass_factory",
# 		"logo": "/assets/glass_factory/logo.png",
# 		"title": "Glass Factory",
# 		"route": "/glass_factory",
# 		"has_permission": "glass_factory.api.permission.has_app_permission"
# 	}
# ]

# Includes in <head>
# ------------------

# include js, css files in header of desk.html
# app_include_css = "/assets/glass_factory/css/glass_factory.css"
# app_include_js = "/assets/glass_factory/js/glass_factory.js"

# include js, css files in header of web template
# web_include_css = "/assets/glass_factory/css/glass_factory.css"
# web_include_js = "/assets/glass_factory/js/glass_factory.js"

# include custom scss in every website theme (without file extension ".scss")
# website_theme_scss = "glass_factory/public/scss/website"

# include js, css files in header of web form
# webform_include_js = {"doctype": "public/js/doctype.js"}
# webform_include_css = {"doctype": "public/css/doctype.css"}

# include js in page
# page_js = {"page" : "public/js/file.js"}

# include js in doctype views
# doctype_js = {"doctype" : "public/js/doctype.js"}
# doctype_list_js = {"doctype" : "public/js/doctype_list.js"}
# doctype_tree_js = {"doctype" : "public/js/doctype_tree.js"}
# doctype_calendar_js = {"doctype" : "public/js/doctype_calendar.js"}

# Svg Icons
# ------------------
# include app icons in desk
# app_include_icons = "glass_factory/public/icons.svg"

# Home Pages
# ----------

# application home page (will override Website Settings)
# home_page = "login"

# website user home page (by Role)
# role_home_page = {
# 	"Role": "home_page"
# }

# Generators
# ----------

# automatically create page for each record of this doctype
# website_generators = ["Web Page"]

# automatically load and sync documents of this doctype from downstream apps
# importable_doctypes = [doctype_1]

# Jinja
# ----------

# add methods and filters to jinja environment
# jinja = {
# 	"methods": "glass_factory.utils.jinja_methods",
# 	"filters": "glass_factory.utils.jinja_filters"
# }

# Installation
# ------------

# before_install = "glass_factory.install.before_install"
# after_install = "glass_factory.install.after_install"

# Uninstallation
# ------------

# before_uninstall = "glass_factory.uninstall.before_uninstall"
# after_uninstall = "glass_factory.uninstall.after_uninstall"

# Integration Setup
# ------------------
# To set up dependencies/integrations with other apps
# Name of the app being installed is passed as an argument

# before_app_install = "glass_factory.utils.before_app_install"
# after_app_install = "glass_factory.utils.after_app_install"

# Integration Cleanup
# -------------------
# To clean up dependencies/integrations with other apps
# Name of the app being uninstalled is passed as an argument

# before_app_uninstall = "glass_factory.utils.before_app_uninstall"
# after_app_uninstall = "glass_factory.utils.after_app_uninstall"

# Build
# ------------------
# To hook into the build process

# after_build = "glass_factory.build.after_build"

# Desk Notifications
# ------------------
# See frappe.core.notifications.get_notification_config

# notification_config = "glass_factory.notifications.get_notification_config"

# Permissions
# -----------
# Permissions evaluated in scripted ways

# permission_query_conditions = {
# 	"Event": "frappe.desk.doctype.event.event.get_permission_query_conditions",
# }
#
# has_permission = {
# 	"Event": "frappe.desk.doctype.event.event.has_permission",
# }

# Document Events
# ---------------
# Hook on document methods and events

# doc_events = {
# 	"*": {
# 		"on_update": "method",
# 		"on_cancel": "method",
# 		"on_trash": "method"
# 	}
# }

# Scheduled Tasks
# ---------------

# scheduler_events = {
# 	"all": [
# 		"glass_factory.tasks.all"
# 	],
# 	"daily": [
# 		"glass_factory.tasks.daily"
# 	],
# 	"hourly": [
# 		"glass_factory.tasks.hourly"
# 	],
# 	"weekly": [
# 		"glass_factory.tasks.weekly"
# 	],
# 	"monthly": [
# 		"glass_factory.tasks.monthly"
# 	],
# }

# Testing
# -------

# before_tests = "glass_factory.install.before_tests"

# Extend DocType Class
# ------------------------------
#
# Specify custom mixins to extend the standard doctype controller.
# extend_doctype_class = {
# 	"Task": "glass_factory.custom.task.CustomTaskMixin"
# }

# Overriding Methods
# ------------------------------
#
# override_whitelisted_methods = {
# 	"frappe.desk.doctype.event.event.get_events": "glass_factory.event.get_events"
# }
#
# each overriding function accepts a `data` argument;
# generated from the base implementation of the doctype dashboard,
# along with any modifications made in other Frappe apps
# override_doctype_dashboards = {
# 	"Task": "glass_factory.task.get_dashboard_data"
# }

# exempt linked doctypes from being automatically cancelled
#
# auto_cancel_exempted_doctypes = ["Auto Repeat"]

# Ignore links to specified DocTypes when deleting documents
# -----------------------------------------------------------

# ignore_links_on_delete = ["Communication", "ToDo"]

# Request Events
# ----------------
# before_request = ["glass_factory.utils.before_request"]
# after_request = ["glass_factory.utils.after_request"]

# Job Events
# ----------
# before_job = ["glass_factory.utils.before_job"]
# after_job = ["glass_factory.utils.after_job"]

# User Data Protection
# --------------------

# user_data_fields = [
# 	{
# 		"doctype": "{doctype_1}",
# 		"filter_by": "{filter_by}",
# 		"redact_fields": ["{field_1}", "{field_2}"],
# 		"partial": 1,
# 	},
# 	{
# 		"doctype": "{doctype_2}",
# 		"filter_by": "{filter_by}",
# 		"partial": 1,
# 	},
# 	{
# 		"doctype": "{doctype_3}",
# 		"strict": False,
# 	},
# 	{
# 		"doctype": "{doctype_4}"
# 	}
# ]

# Authentication and authorization
# --------------------------------

# auth_hooks = [
# 	"glass_factory.auth.validate"
# ]

# Automatically update python controller files with type annotations for this app.
# export_python_type_annotations = True

# default_log_clearing_doctypes = {
# 	"Logging DocType Name": 30  # days to retain logs
# }

# Translation
# ------------
# List of apps whose translatable strings should be excluded from this app's translations.
# ignore_translatable_strings_from = []

