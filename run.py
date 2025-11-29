from voyager import Voyager
import os
os.environ["LANGCHAIN_TRACING_V2"]="false"; os.environ["LANGSMITH_TRACING"]="false"
# You can also use mc_port instead of azure_login, but azure_login is highly recommended
openai_api_key = "s"

v = Voyager(mc_port=41907, openai_api_key=openai_api_key,resume=True)  # portu kendininkiyle değiştir
v.learn()
