OS-X-Folder-Actions
===================
Found these here: http://j4zzcat.wordpress.com/2010/01/06/folder-actions-unix-style.

## Folder Actions, UNIX style

Mac OS X has a nice feature called Folder Actions. Basically, this feature lets you attach an AppleScript script to a folder, and have that script run whenever items are added or removed from the folder. Have a look here for a simple example.

How would you write that script in python? Here’s my simple, general-purpose solution.

There are three parts in this solution:

1. An AppleScript called Send Events To Shell Script.scpt (binary file, use the built-in AppleScript Editor to view/edit)

1. A script called FolderActionsDispatcher.sh/FolderActionsDispatcher.py

1. A python script .FolderActions.py to handle rules of .FolderActions.yaml in the target directory

When you attach the Send Events To Shell Script.scpt script to a folder, it will act as an observer and forward the Opening, Closing, Adding and Removing events to the script /usr/local/bin/FolderActionsDispatcher.sh/FolderActionsDispatcher.py. The event payload includes the type of the event, the data needed to perform its purpose (i.e., for the Adding event, the list of the added items), as well as the name of the folder that was the target of the event. FolderActionsDispatcher.py will parse the event, and then will try to invoke a callback script named .FolderActions.py. All you have to do is write the .FolderActions.yaml config file and place it in the folder it belongs to.

## Installation

Here’s an example. Let’s say that we want to copy every file placed in ~/Downloads to some directory, and do it automatically. Here’s what we will do:

1. One time setup:
 
   1. Clone this repo.
   2. Copy **Send Events To Shell Script.scpt** to **~/Library/Scripts/Folder Action Scripts**. 
   3. Copy **FolderActionsDispatcher.sh** and **FolderActionsDispatcher.py** to **/usr/local/bin**.
   4. Make it executable, like so: _$ chmod a+x /usr/local/bin/FolderActionsDispatcher.sh_.
   5. Create python virtual env using python3 -m venv ~/.venvs/systools.
   6. pip install pyyaml
   7. Copy **.FolderActions.py** to **/usr/local/bin**
   8. Make **.FolderActions.yaml** in target directory

2. Create the file ~/Downloads/.FolderActions.yaml. The file .FolderActions.yaml is a good starting point.

3. Enable Folder Actions for ~/Downloads. In the Finder application, select the ~/Downloads folder, bring up the context menu, and select ‘Folder Actions Setup…‘ From the dialog, select the ‘Send Events To Shell Script.scpt‘ action, and click the ‘Attach‘ button.

That’s it :-) To test it, place a file in ~/Downloads and see that it gets copied to some directory by rules.
