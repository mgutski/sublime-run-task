import sublime
import sublime_plugin
import os
import json
import subprocess
import shlex
import threading


SUBLIME_FOLDER_NAME = ".sublime"
TASKS_FILE_NAME = "tasks.json"

JSON_TASKS_KEY = "tasks"
JSON_TASK_LABEL_KEY = "label"
JSON_TASK_TYPE_KEY = "type"
JSON_TASK_COMMAND_KEY = "command"
JSON_TASK_ARGS_KEY = "args"
JSON_TASK_SHOW_OUTPUT_PANEL_KEY = "show_output_panel"

SUBLIME_TASK_TYPE = "sublime"
SHELL_TASK_TYPE = "shell"

VARIABLE_CWD = "${cwd}"

def find_directory(path, name):
	directory = next((directory for directory in os.listdir(path) if os.path.isdir(os.path.join(path, directory)) and directory == name), None)
	if directory:
		return os.path.join(path, directory)
	return None

def find_file(path, name):
	file = next((file for file in os.listdir(path) if os.path.isfile(os.path.join(path, file)) and file == name), None)
	if file:
		return os.path.join(path, file)
	return None


class ShellTaskThread(threading.Thread):
	def __init__(self, window, args=[], cwd=None, show_output_panel=True):
		super(ShellTaskThread, self).__init__(self)
		self.window = window
		self.args = args
		self.cwd = cwd
		self.show_output_panel = show_output_panel

	def run(self):
		if self.show_output_panel:
			output_panel = self.window.create_output_panel("RunTask")
			self.window.run_command("show_panel", {"panel": "output.RunTask"})
			with subprocess.Popen(self.args, bufsize=1, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, cwd=self.cwd, universal_newlines=True) as proc:
				out_line = proc.stdout.read()
				output_panel.run_command("append", {"characters": out_line})
		else:
			subprocess.Popen(self.args, cwd=self.cwd)


class Task():
	def __init__(self, label, task_type, command, args, show_output_panel):
		self.label = label
		self.task_type = task_type
		self.command = command
		self.args = args
		self.show_output_panel = show_output_panel

	def get_label(self):
		return self._label

	def set_label(self, value):
		self._label = value

	label = property(get_label, set_label)

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
		if self.task_type == SUBLIME_TASK_TYPE:
			window.run_command(self.command, self.args)
		elif self.task_type == SHELL_TASK_TYPE:
			args = [self.command]
			if type(self.args) is list:
				args.extend(self.args)
			elif type(self.args) is str:
				args.extend(shlex.split(self.args.strip()))
			for idx, arg in enumerate(args):
				args[idx] = arg.replace(VARIABLE_CWD, cwd)
			ShellTaskThread(window, args, cwd, self.show_output_panel).start()


class TasksParser():
	def parse_tasks(self, tasks_json):
		tasks = []
		if type(tasks_json) is not dict:
			sublime.error_message('Run Task: Invalid JSON format')
			return tasks
		if JSON_TASKS_KEY in tasks_json and type(tasks_json[JSON_TASKS_KEY]) is list:
			for idx, task_json in enumerate(tasks_json[JSON_TASKS_KEY]):
				task = self.parse_task(task_json, idx)
				if task is not None:
					tasks.append(task)
		else:
			sublime.error_message('Run Task: Invalid JSON - expected "tasks" list')
		return tasks

	def parse_task(self, task_json, task_index):
		label, task_type, command, args, show_output_panel = (None, None, None, None, True)
		if type(task_json) is dict:
			if JSON_TASK_LABEL_KEY in task_json:
				label = self.parse_task_label(task_json[JSON_TASK_LABEL_KEY])
			if JSON_TASK_TYPE_KEY in task_json:
				task_type = self.parse_task_type(task_json[JSON_TASK_TYPE_KEY])
			if JSON_TASK_COMMAND_KEY in task_json:
				command = self.parse_task_command(task_json[JSON_TASK_COMMAND_KEY])
			if JSON_TASK_ARGS_KEY in task_json:
				args = self.parse_task_args(task_type, task_json[JSON_TASK_ARGS_KEY])
			if JSON_TASK_SHOW_OUTPUT_PANEL_KEY in task_json:
				show_output_panel = self.parse_task_show_output_panel(task_type, task_json[JSON_TASK_SHOW_OUTPUT_PANEL_KEY])
		if label is None or task_type is None or command is None or show_output_panel is None:
			sublime.error_message('Run Task: Invalid task number ' + str(task_index) + ' definition')
			return None
		return Task(label, task_type, command, args, show_output_panel)

	def parse_task_label(self, task_label):
		if type(task_label) is not str or task_label.strip() == "":
			return None
		return task_label.strip()

	def parse_task_type(self, task_type):
		if type(task_type) is not str or (task_type != SUBLIME_TASK_TYPE and task_type != SHELL_TASK_TYPE):
			return None
		return task_type

	def parse_task_command(self, task_command):
		if type(task_command) is not str or task_command.strip() == "":
			return None
		return task_command.strip()

	def parse_task_args(self, task_type, task_args):
		if task_type == SUBLIME_TASK_TYPE and type(task_args) is dict:
			return task_args
		if task_type == SHELL_TASK_TYPE and (type(task_args) is str or type(task_args) is list):
			return task_args
		return None

	def parse_task_show_output_panel(self, task_type, task_show_output_panel):
		if type(task_show_output_panel) is not bool:
			return None
		return task_show_output_panel


class RunTaskCommand(sublime_plugin.WindowCommand):
	def run(self):
		folders = self.window.folders()
		if not folders or len(folders) == 0:
			return

		self.workspace = folders[0]
		self.tasks = []

		sublime_folder = find_directory(self.workspace, SUBLIME_FOLDER_NAME)

		if sublime_folder:
			tasks_file = find_file(sublime_folder, TASKS_FILE_NAME)
			if tasks_file:
				with open(tasks_file, "r") as fp:
					try:
						tasks_json = json.load(fp)
						self.tasks = TasksParser().parse_tasks(tasks_json)
					except ValueError:
						pass
				if len(self.tasks) > 0:
					labels = list(map(lambda task: task.label, self.tasks))
					self.window.show_quick_panel(labels, self.on_done, sublime.MONOSPACE_FONT, 0, None)

	def on_done(self, taskIndex):
		if taskIndex < 0 or taskIndex >= len(self.tasks):
			return
		selectedTask = self.tasks[taskIndex]
		print('Run Task: Running task "' + selectedTask.label + '"')
		selectedTask.execute(window=self.window, cwd=self.workspace)
