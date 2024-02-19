import sublime
import sublime_plugin
import fnmatch
import os
import json
import subprocess
import shlex
import threading


PLUGIN_NAME = "Run Task"

PROJECT_FILE_NAME_PATTERN = "*.sublime-project"
OUTPUT_PANEL_NAME = "run_task"

JSON_TASKS_KEY = PLUGIN_NAME + ".tasks"
JSON_TASK_NAME_KEY = "name"
JSON_TASK_TYPE_KEY = "type"
JSON_TASK_COMMAND_KEY = "command"
JSON_TASK_ARGS_KEY = "args"
JSON_TASK_SHOW_OUTPUT_PANEL_KEY = "show_output_panel"
JSON_TASK_WINDOWS_CONFIG_KEY = "windows"

JSON_TASK_SHOW_OUTPUT_PANEL_DEFAULT_VALUE = True

SUBLIME_TASK_TYPE = "sublime"
SHELL_TASK_TYPE = "shell"

VARIABLE_CWD = "${cwd}"
VARIABLE_FILE = "${file}"


class OSUtils():
	@staticmethod
	def find_directory(path, name):
		directory = next((directory for directory in os.listdir(path) if os.path.isdir(os.path.join(path, directory)) and directory == name), None)
		if directory:
			return os.path.join(path, directory)
		return None

	@staticmethod
	def find_file(path, name):
		file = next((file for file in os.listdir(path) if os.path.isfile(os.path.join(path, file)) and file == name), None)
		if file:
			return os.path.join(path, file)
		return None

	@staticmethod
	def find_file_with_pattern(path, pattern):
		file = next((file for file in os.listdir(path) if os.path.isfile(os.path.join(path, file)) and fnmatch.fnmatch(file, pattern)), None)
		if file:
			return os.path.join(path, file)
		return None

	@staticmethod
	def is_windows():
		return os.name == 'nt'


class ErrorMessage():

	EXPECTED_BOOL_VALUE = 'Expected a boolean value.'
	EXPECTED_JSON_OBJECT = 'Expected a JSON object.'
	EXPECTED_STRING_OR_ARRAY = 'Expected a string or array value.'
	EXPECTED_NON_EMPTY_STRING = 'Expected a non-empty string with at least one non-whitespace character.'
	EXPECTED_TASK_TYPE = 'Expected "' + SHELL_TASK_TYPE + '" or "' + SUBLIME_TASK_TYPE + '".'

	CHECK_CONFIGURATION_FILE = 'Please check the .sublime-project file.'

	@staticmethod
	def invalid_json():
		return PLUGIN_NAME + ': Invalid JSON. ' + ErrorMessage.CHECK_CONFIGURATION_FILE

	@staticmethod
	def invalid_json_object(expected_type_name=None):
		error_message = PLUGIN_NAME + ': Invalid JSON object. '
		if expected_type_name is not None:
			error_message += 'Expected ' + expected_type_name + '. '
		error_message += ErrorMessage.CHECK_CONFIGURATION_FILE
		return error_message

	@staticmethod
	def invalid_json_task_definition(task_index, task_error_message=None):
		error_message = PLUGIN_NAME + ': Invalid JSON task definition at index ' + str(task_index) + '. '
		error_message += ErrorMessage.CHECK_CONFIGURATION_FILE
		if task_error_message is not None:
			error_message += '\n\nError message: ' + task_error_message
		return error_message

	@staticmethod
	def invalid_field_value(field_name, value_error_message=None):
		error_message = 'Invalid value for field "' + field_name + '".'
		if value_error_message is not None:
			error_message += ' ' + value_error_message
		return error_message

	@staticmethod
	def missing_required_field(field_name):
		return 'Missing required field "' + field_name + '".'

	@staticmethod
	def task_execution_failed(task_name, execution_error_message=None):
		error_message = PLUGIN_NAME + ': Execution failed for task "' + task_name + '".'
		if execution_error_message is not None:
			error_message += '\n\nError message: ' + execution_error_message
		return error_message


class OutputPanel():
	def __init__(self, window):
		self.window = window
		self.panel_view = None

	def show(self):
		self.panel_view = self.__create_panel_view()
		self.window.run_command("show_panel", {"panel": "output." + OUTPUT_PANEL_NAME})

	def write(self, message):
		if self.panel_view is not None:
			self.panel_view.run_command("append", {"characters": message})

	def __create_panel_view(self):
		return self.window.create_output_panel(OUTPUT_PANEL_NAME)


class ShellTaskThread(threading.Thread):
	def __init__(self, task_name, window, args=[], cwd=None, show_output_panel=False, has_file=False):
		super(ShellTaskThread, self).__init__(self)
		self.task_name = task_name
		self.window = window
		self.args = args
		self.cwd = cwd
		self.show_output_panel = show_output_panel
		self.has_file = has_file

	def run(self):
		if self.show_output_panel:
			output_panel = OutputPanel(self.window)
			output_panel.show()
			try:
				process = subprocess.Popen(self.args, bufsize=1, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, cwd=self.cwd, universal_newlines=True)
			except Exception as ex:
				sublime.error_message(ErrorMessage.task_execution_failed(self.task_name, str(ex)))
				return
			while True:
				out_line = process.stdout.readline()
				if out_line == '' and process.poll() is not None:
					finish_msg = "| Task finished with return code " + str(process.poll()) + " |"
					finish_msg = "\n" + ("-" * len(finish_msg)) + "\n" + finish_msg + "\n" + ("-" * len(finish_msg))
					output_panel.write(finish_msg)
					if self.has_file:
						self.window.focus_view(self.window.active_view())
					break
				if out_line:
					output_panel.write(out_line)

		else:
			if not self.has_file:
				try:
					subprocess.Popen(self.args, cwd=self.cwd)
				except Exception as ex:
					sublime.error_message(ErrorMessage.task_execution_failed(self.task_name, str(ex)))
			else:
				try:
					si = subprocess.STARTUPINFO()
					si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
					si.wShowWindow = subprocess.SW_HIDE # default
					process = subprocess.Popen(self.args, bufsize=1, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, cwd=self.cwd, universal_newlines=True, startupinfo=si)
				except Exception as ex:
					sublime.error_message(ErrorMessage.task_execution_failed(self.task_name, str(ex)))
				while True:
					out_line = process.stdout.readline()
					if out_line:
						print(out_line)

					if out_line == '' and process.poll() is not None:
						if self.has_file:
							self.window.focus_view(self.window.active_view())
						break




class Task():
	def __init__(self, name, task_type, command, args, show_output_panel):
		self.name = name
		self.task_type = task_type
		self.command = command
		self.args = args
		self.show_output_panel = show_output_panel

	def get_name(self):
		return self._name

	def set_name(self, value):
		self._name = value

	name = property(get_name, set_name)

	def get_task_type(self):
		return self._task_type

	def set_task_type(self, value):
		self._task_type = value

	task_type = property(get_task_type, set_task_type)

	def get_command(self):
		return self._command

	def set_command(self, value):
		self._command = value

	command = property(get_command, set_command)

	def get_args(self):
		return self._args

	def set_args(self, value):
		self._args = value

	args = property(get_args, set_args)

	def get_show_output_panel(self):
		return self._show_output_panel

	def set_show_output_panel(self, value):
		self._show_output_panel = value

	show_output_panel = property(get_show_output_panel, set_show_output_panel)

	def execute(self, window, cwd):
		variables = window.extract_variables()
		file = variables['file']
		if self.task_type == SUBLIME_TASK_TYPE:
			window.run_command(self.command, self.args)
		elif self.task_type == SHELL_TASK_TYPE:
			args = [self.command]
			if type(self.args) is list:
				args.extend(self.args)
			elif type(self.args) is str:
				args.extend(shlex.split(self.args.strip()))
			has_file = False
			for idx, arg in enumerate(args):
				args[idx] = arg.replace(VARIABLE_CWD, cwd)
				has_file = has_file or VARIABLE_FILE in args[idx]
				args[idx] = arg.replace(VARIABLE_FILE, file)
			ShellTaskThread(self.name, window, args, cwd, self.show_output_panel, has_file).start()


class TaskParser():
	def parse_tasks(self, tasks_json):
		tasks = []
		if type(tasks_json) is not dict:
			return None, ErrorMessage.invalid_json_object()
		if JSON_TASKS_KEY in tasks_json and type(tasks_json[JSON_TASKS_KEY]) is list:
			parse_error_msg = None
			for idx, task_json in enumerate(tasks_json[JSON_TASKS_KEY]):
				task, parse_error_msg = self.parse_task(task_json)
				if task is not None:
					tasks.append(task)
				elif parse_error_msg is not None:
					return None, ErrorMessage.invalid_json_task_definition(idx, parse_error_msg)
		else:
			return None, ErrorMessage.invalid_json_object(expectedTypeName='"' + JSON_TASKS_KEY + '" array')
		return tasks, None

	def parse_task(self, task_json):
		name, task_type, command, args, windows_config, show_output_panel = (None, None, None, None, None, None)
		parse_error_msg = None
		if type(task_json) is dict:
			name, parse_error_msg = self.__parse_task_name(task_json)
			if parse_error_msg is None:
				task_type, parse_error_msg = self.__parse_task_type(task_json)
			if parse_error_msg is None:
				windows_config, parse_error_msg = self.__parse_task_windows_config(task_json)
			if parse_error_msg is None:
				if windows_config is not None:
					command, parse_error_msg = self.__parse_task_command(windows_config)
					if parse_error_msg is None:
						args, parse_error_msg = self.__parse_task_args(task_type, windows_config)
				else:
					command, parse_error_msg = self.__parse_task_command(task_json)
					if parse_error_msg is None:
						args, parse_error_msg = self.__parse_task_args(task_type, task_json)
			if parse_error_msg is None:
				show_output_panel, parse_error_msg = self.__parse_task_show_output_panel(task_json)
		if parse_error_msg is not None:
			return None, parse_error_msg
		return Task(name, task_type, command, args, show_output_panel), None

	def __parse_task_name(self, task_json):
		task_name = None
		if JSON_TASK_NAME_KEY in task_json:
			task_name = task_json[JSON_TASK_NAME_KEY]
		else:
			return None, ErrorMessage.missing_required_field(JSON_TASK_NAME_KEY)
		if type(task_name) is not str or task_name.strip() == "":
			return None, ErrorMessage.invalid_field_value(JSON_TASK_NAME_KEY, ErrorMessage.EXPECTED_NON_EMPTY_STRING)
		return task_name.strip(), None

	def __parse_task_type(self, task_json):
		task_type = None
		if JSON_TASK_TYPE_KEY in task_json:
			task_type = task_json[JSON_TASK_TYPE_KEY]
		else:
			return None, ErrorMessage.missing_required_field(JSON_TASK_TYPE_KEY)
		if type(task_type) is not str or (task_type != SUBLIME_TASK_TYPE and task_type != SHELL_TASK_TYPE):
			return None, ErrorMessage.invalid_field_value(JSON_TASK_TYPE_KEY, ErrorMessage.EXPECTED_TASK_TYPE)
		return task_type, None

	def __parse_task_command(self, task_json):
		task_command = None
		if JSON_TASK_COMMAND_KEY in task_json:
			task_command = task_json[JSON_TASK_COMMAND_KEY]
		else:
			return None, ErrorMessage.missing_required_field(JSON_TASK_COMMAND_KEY)
		if type(task_command) is not str or task_command.strip() == "":
			return None, ErrorMessage.invalid_field_value(JSON_TASK_COMMAND_KEY, ErrorMessage.EXPECTED_NON_EMPTY_STRING)
		return task_command.strip(), None

	def __parse_task_args(self, task_type, task_json):
		task_args = None
		if JSON_TASK_ARGS_KEY in task_json:
			task_args = task_json[JSON_TASK_ARGS_KEY]
		else:
			return None, None
		if task_type == SUBLIME_TASK_TYPE:
			if type(task_args) is dict:
				return task_args, None
			else:
				return None, ErrorMessage.invalid_field_value(JSON_TASK_ARGS_KEY, ErrorMessage.EXPECTED_JSON_OBJECT)
		if task_type == SHELL_TASK_TYPE:
			if (type(task_args) is str or type(task_args) is list):
				return task_args, None
			else:
				return None, ErrorMessage.invalid_field_value(JSON_TASK_ARGS_KEY, ErrorMessage.EXPECTED_STRING_OR_ARRAY)
		return None, ErrorMessage.invalid_field_value(JSON_TASK_ARGS_KEY)

	def __parse_task_windows_config(self, task_json):
		task_windows_config = None
		if JSON_TASK_WINDOWS_CONFIG_KEY in task_json and OSUtils.is_windows():
			task_windows_config = task_json[JSON_TASK_WINDOWS_CONFIG_KEY]
		else:
			return None, None
		if type(task_windows_config) is not dict:
			return None, ErrorMessage.invalid_field_value(JSON_TASK_WINDOWS_CONFIG_KEY, ErrorMessage.EXPECTED_JSON_OBJECT)
		return task_windows_config, None

	def __parse_task_show_output_panel(self, task_json):
		task_show_output_panel = None
		if JSON_TASK_SHOW_OUTPUT_PANEL_KEY in task_json:
			task_show_output_panel = task_json[JSON_TASK_SHOW_OUTPUT_PANEL_KEY]
		else:
			return JSON_TASK_SHOW_OUTPUT_PANEL_DEFAULT_VALUE, None
		if type(task_show_output_panel) is not bool:
			return None, ErrorMessage.invalid_field_value(JSON_TASK_SHOW_OUTPUT_PANEL_KEY, ErrorMessage.EXPECTED_BOOL_VALUE)
		return task_show_output_panel, None


class RunTaskCommand(sublime_plugin.WindowCommand):
	def run(self):
		folders = self.window.folders()
		if not folders or len(folders) == 0:
			return

		self.workspace = folders[0]
		self.tasks = []

		project_file = OSUtils.find_file_with_pattern(self.workspace, PROJECT_FILE_NAME_PATTERN)
		if project_file:
			with open(project_file, "r") as fp:
				try:
					tasks_json = json.load(fp)
					self.tasks, parse_error_msg = TaskParser().parse_tasks(tasks_json)
					if parse_error_msg is not None:
						sublime.error_message(parse_error_msg)
						return
				except ValueError:
					sublime.error_message(ErrorMessage.invalid_json())
					return
			if len(self.tasks) > 0:
				labels = list(map(lambda task: task.name, self.tasks))
				self.window.show_quick_panel(labels, self.on_done, sublime.MONOSPACE_FONT, 0, None)

	def on_done(self, taskIndex):
		if taskIndex < 0 or taskIndex >= len(self.tasks):
			return
		selectedTask = self.tasks[taskIndex]
		print(PLUGIN_NAME + ': Running task "' + selectedTask.name + '"')
		selectedTask.execute(window=self.window, cwd=self.workspace)
