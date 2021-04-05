import os
import time

while True:
    print(os.environ['HOME'])
    print(os.environ['XDG_DATA_HOME'])
    time.sleep(5)
