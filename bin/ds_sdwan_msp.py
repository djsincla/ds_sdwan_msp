import os
import os.path
import sys
import time
import requests
import json
import collections
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "lib"))
import splunklib.client as client
from splunklib.modularinput import *
import datetime
from datetime import datetime,timedelta
from state_store import FileStateStore
import urllib3
urllib3.disable_warnings()

# Dwayne Sinclair / djs 06/19 - 09/23
#
# VeloCloud REST API to Splunk based on the following examples:
#  https://www.function1.com/2017/02/encrypting-a-modular-input-field-without-setup-xml
#  https://www.function1.com/2016/08/splunk-modular-inputs
#  https://www.baboonbones.com/blog/category/splunk-development/
# Splunk Documenattion:
#  https://www.splunk.com/en_us/blog/tips-and-tricks/splunking-continuous-rest-data.html
#  https://docs.splunk.com/Documentation/Splunk/latest/Admin/Listofconfigurationfiles
#  https://conf.splunk.com/files/2016/slides/best-practices-for-developing-splunk-apps-and-add-ons.pdf
#  https://splunkbase.splunk.com/app/1621/
#  https://splunkbase.splunk.com/app/282/
# Logging:
#  We use ew.log to log errors. This can be reviewed in ../Splunk/var/log/splunk/splunkd.log
#  To find log records, tail and grep on: velocloud

class MyScript(Script):

	# Define some global variables
	MASK           = "<nothing to see here>"
	APP            = __file__.split(os.sep)[-3]

	def get_scheme(self):

		# djs 12/19
		# A code method to create inputs without the need to use xml.
		scheme = Scheme("SD-WAN VCO MSP Event Log")
		scheme.description = ("Extract the VMware SD-WAN MSP Event Log from VCO via REST API")
		scheme.use_external_validation = True
		scheme.streaming_mode_xml = True
		scheme.use_single_instance = False

		arg = Argument(
			name="rest_url",
			title="VCO URL",
			data_type=Argument.data_type_string,
			required_on_create=True,
			required_on_edit=True
		)
		scheme.add_argument(arg)

		arg = Argument(
			name="username",
			title="Username",
			data_type=Argument.data_type_string,
			required_on_create=True,
			required_on_edit=True
		)
		scheme.add_argument(arg)

		arg = Argument(
			name="vcoToken",
			title="VCO API Token",
			data_type=Argument.data_type_string,
			required_on_create=True,
			required_on_edit=True
		)
		scheme.add_argument(arg)

		return scheme

	def validate_input(self, definition):
		session_key = definition.metadata["session_key"]
		rest_url 	= definition.parameters["rest_url"]
		username    = definition.parameters["username"]
		vcoToken	= definition.parameters["vcoToken"]

		try:
			# Do checks here.  For example, try to connect to whatever you need the credentials for using the credentials provided.
			# If everything passes, create a credential with the provided input.

			urlSecure , unused = rest_url.split("://")
			if urlSecure.lower() != "https":
				raise ValueError("VCO URL must start with https. Found %s" % rest_url)

			pass

		except Exception as e:
			raise Exception("Something did not go right in input validation: %s" % str(e))

	def encrypt_vcoToken(self, username, vcoToken, session_key):
		args = {'token':session_key}
		service = client.connect(**args)
		
		try:
			# If the credential already exists, delete it.
			for storage_password in service.storage_passwords:
				if storage_password.username == username:
					service.storage_passwords.delete(username=storage_password.username)
					break

			# Create the credential.
			service.storage_passwords.create(vcoToken, username)

		except Exception as e:
			raise Exception("An error occurred updating credentials. Please ensure your user account has admin_all_objects and/or list_storage_passwords capabilities. Details: %s" % str(e))

	def mask_vcoToken(self, session_key, rest_url, username):
		try:
			args = {'token':session_key}
			service = client.connect(**args)
			kind, input_name = self.input_name.split("://")
			item = service.inputs.__getitem__((input_name, kind))
			
			kwargs = {
				"rest_url": rest_url,
				"username": username,
				"vcoToken": self.MASK
			}
			item.update(**kwargs).refresh()
			
		except Exception as e:
			raise Exception("Error updating inputs.conf: %s" % str(e))

	def get_vcoToken(self, session_key, username):
		args = {'token':session_key}
		service = client.connect(**args)

		for storage_password in service.storage_passwords:
			if storage_password.username == username:
				return storage_password.content.clear_password

	def stream_events(self, inputs, ew):
		# djs 01/20 
		# Load parameters associated with this modular input.
		self.input_name, self.input_items = inputs.inputs.popitem()
		session_key = self._input_definition.metadata["session_key"]
		unused , inputNameS = self.input_name.split("://")

		username = self.input_items["username"]
		vcoToken = self.input_items['vcoToken']
		rest_url = self.input_items["rest_url"]
		interval  = int(self.input_items["interval"])

		try:
			# If the password is not masked, mask it.
			if vcoToken != self.MASK:
				self.encrypt_vcoToken(username, vcoToken, session_key)
				self.mask_vcoToken(session_key, rest_url, username)

		except Exception as e:
			ew.log("ERROR", "Error: %s" % str(e))

		try:
			# If the vcoToken is not masked, mask it.
			if vcoToken != self.MASK:
				self.encrypt_vcoToken(username, vcoToken, session_key)
				self.mask_vcoToken(session_key, rest_url, username)

		except Exception as e:
			ew.log("ERROR", "Error: %s" % str(e))

		# djs 12/19 
		# Establish a file state store object based on part of the input name.
		state_store = FileStateStore(inputs.metadata, self.input_name)

		if True:

			# djs 12/19
			# We read last position or 0.
			# We read last time logged or default to 180 days ago.
			lastPosition = state_store.get_state(inputNameS+"_Velo_last_pos") or 0
			ew.log('INFO', "Last Position read is: " + str(lastPosition) + " for: " + inputNameS)

			lastTimeLogged = state_store.get_state(inputNameS+"_Velo_last_time") or (datetime.utcnow() - timedelta(days=(1))).strftime('%Y-%m-%dT%H:%M:%S.%fZ')
			lastTime_obj = datetime.strptime(lastTimeLogged, '%Y-%m-%dT%H:%M:%S.%fZ') - timedelta(seconds=interval)
			lastTime = lastTime_obj.strftime('%Y-%m-%dT%H:%M:%S.%fZ')
			ew.log('INFO', "Last Time Logged is: " + str(lastTime) + " for: " + inputNameS)
		
			# djs 12/19
			# Format the api call to velocloud vco to obtain event data. 
			eventStart = lastTime
			eventEnd = datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%S.%fZ')
			data = {"interval": {"end": eventEnd, "start": eventStart}}
			ew.log('INFO', "Request to VCO is: " + str(data) + " for: " + inputNameS)

			# djs 03/22 
			# We are requesting MSP Events from VCO.
			veloEventUrl = rest_url+"/portal/rest/event/getProxyEvents"

			# djs 03/21 Pull the vcoToken and format for header.
			clearVcoToken = self.get_vcoToken(session_key, username)
			headers = {'Authorization': 'Token ' + clearVcoToken }

			# djs 05/23 If successful, we received a response from VCO.
			respE = requests.post(veloEventUrl, data=json.dumps(data), headers=headers)

			# djs 03/21 Clear token out of memory
			headers = ''
			clearVcoToken = ''
			
			# djs 01/20
			# Debugging only.
			# ew.log('INFO', "Response from VCO: " + respE.text)

			# djs 12/19
			# The data response from the velocloud api call is in resp.text
			outputS =  collections.OrderedDict()
			output = json.loads(respE.text)
			respE = ''

			try:
				# djs 12/19 
				# Each log entry in json response is in data section identified by id.
				# Using id as key, write to a ordered dictionary so we can sort.
				
				for entry in output['data']:
					thisId = entry['id']
					outputS[thisId] = entry

				ew.log('INFO', str(len(outputS)) + " records returned from VCO Request for: " + inputNameS )
				
				if len(outputS) > 0:
					# djs 12/19 
					# From VeloCloud, records are in the wrong order so we
					# re-sort the ordered dictionary so oldest events first. 
					outputSr = collections.OrderedDict(reversed(list(outputS.items())))
                    
					# djs 12/19 
					# For each event, write to splunk using ew.write_event and event object
					# Note assumption is key is always getting larger. We dont handle wrapping.
					highId = 0
					eventCount = 0
					for key_str, value in list(outputSr.items()):
						key = int(key_str)
						if key > highId:
							highId = key
						if key > int(lastPosition):
							event = Event()
							event.stanza = inputNameS
							event.data = json.dumps(value)
							eventCount += 1
							ew.write_event(event)
				
					# djs 12/19
					# Write the highest event id back to the file state store
					if highId > 0:
						try:
							# djs 01/20
							# Save the last time and position we wrote to splunk in state store.
							state_store.update_state(inputNameS+"_Velo_last_pos", str(highId))
							ew.log('INFO', "Last Position out is: " + str(highId) + " for: " + inputNameS)

							state_store.update_state(inputNameS+"_Velo_last_time", str(eventEnd))
							ew.log('INFO', "Last Time out is: " + str(eventEnd) + " for: " + inputNameS)

						except Exception as e:
							raise Exception("Something did not go right: %s" % str(e))

					ew.log('INFO', str(eventCount) + " VeloCloud events written to log for: " + inputNameS)

			except Exception as e:
				raise Exception("Something did not go right. Likely a bad password: %s" % str(e))

if __name__ == "__main__":
	exitcode = MyScript().run(sys.argv)
	sys.exit(exitcode)
