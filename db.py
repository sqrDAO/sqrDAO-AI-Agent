import sqlite3
from datetime import datetime
from typing import List, Tuple, Optional
import json
import logging
from config import DATABASE_FILE

logger = logging.getLogger(__name__)

class Database:
    def __init__(self):
        self.conn = sqlite3.connect(DATABASE_FILE)
        self.cursor = self.conn.cursor()
        self.setup_database()

    def setup_database(self):
        """Create necessary tables if they don't exist."""
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS conversations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                message TEXT,
                response TEXT,
                timestamp DATETIME,
                context TEXT
            )
        ''')
        
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS knowledge_base (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                topic TEXT,
                information TEXT,
                source TEXT,
                timestamp DATETIME
            )
        ''')
        self.conn.commit()

    def store_conversation(self, user_id: int, message: str, response: str, context: Optional[str] = None) -> None:
        """Store a conversation in the database."""
        try:
            self.cursor.execute('''
                INSERT INTO conversations (user_id, message, response, timestamp, context)
                VALUES (?, ?, ?, ?, ?)
            ''', (user_id, message, response, datetime.now(), context))
            self.conn.commit()
        except Exception as e:
            logger.error(f"Error storing conversation: {str(e)}")
            raise

    def store_knowledge(self, topic: str, information: str, source: Optional[str] = None) -> None:
        """Store knowledge in the database."""
        try:
            self.cursor.execute('''
                INSERT INTO knowledge_base (topic, information, source, timestamp)
                VALUES (?, ?, ?, ?)
            ''', (topic, information, source, datetime.now()))
            self.conn.commit()
        except Exception as e:
            logger.error(f"Error storing knowledge: {str(e)}")
            raise

    def get_relevant_context(self, user_id: int, message: str, limit: int = 5) -> List[Tuple[str, str, Optional[str]]]:
        """Get relevant context from previous conversations."""
        try:
            keywords = message.lower().split()
            query = '''
                SELECT message, response, context
                FROM conversations
                WHERE user_id = ? AND (
            ''' + ' OR '.join(['lower(message) LIKE ?' for _ in keywords]) + ')'
            params = [user_id] + ['%' + keyword + '%' for keyword in keywords]
            
            self.cursor.execute(query + ' ORDER BY timestamp DESC LIMIT ?', 
                              params + [limit])
            return self.cursor.fetchall()
        except Exception as e:
            logger.error(f"Error getting relevant context: {str(e)}")
            return []

    def get_knowledge(self, topic: str) -> List[Tuple[str]]:
        """Get knowledge from the database."""
        try:
            self.cursor.execute('''
                SELECT information
                FROM knowledge_base
                WHERE lower(topic) LIKE lower(?)
            ''', ('%' + topic + '%',))
            results = self.cursor.fetchall()
            # Try to parse JSON data if possible
            parsed_results = []
            for result in results:
                try:
                    # Attempt to parse as JSON
                    parsed_data = json.loads(result[0])
                    parsed_results.append(parsed_data)
                except json.JSONDecodeError:
                    # If not JSON, keep original data
                    parsed_results.append(result[0])
            return parsed_results
        except Exception as e:
            logger.error(f"Error getting knowledge: {str(e)}")
            return []

    def close(self) -> None:
        """Close the database connection."""
        self.conn.close()

    def add_group(self, chat_id, groupname, bot_data):
        """Add the group to the 'groups' table and update bot_data."""
        try:
            # Retrieve existing groups
            existing_groups = self.get_knowledge("groups")
            if isinstance(existing_groups, str):
                existing_groups = json.loads(existing_groups)  # Parse if it's a JSON string

            # Flatten the nested list if necessary
            if isinstance(existing_groups, list) and len(existing_groups) > 0:
                existing_groups = existing_groups[0]  # Get the first (and only) list

            # Log the type and content of existing_groups
            logger.info(f"Existing groups: {existing_groups} (type: {type(existing_groups)})")

            # Ensure existing_groups is a list
            if not isinstance(existing_groups, list):
                logger.error(f"Expected a list of groups, but got {type(existing_groups)}")
                return

            # Check if the group already exists
            if any(group['id'] == chat_id for group in existing_groups):
                logger.warning(f"Group {chat_id} already exists in the knowledge base.")
                return

            # Add new group with the provided groupname
            new_group = {
                'id': chat_id,
                'title': groupname,  # Use the provided groupname
                'type': 'group',
                'added_at': datetime.now().isoformat()
            }
            existing_groups.append(new_group)

            # Update bot_data
            if 'group_members' not in bot_data:
                bot_data['group_members'] = []
            bot_data['group_members'].append(new_group)  # Add the new group to bot_data

            # Store updated groups in the knowledge base
            self.store_knowledge("groups", json.dumps([existing_groups]))  # Wrap in a list again
            logger.info(f"Group {chat_id} added to the knowledge base.")
            logger.info(f"Grouplist after adding group: {self.get_knowledge('groups')}")

        except Exception as e:
            logger.error(f"Error adding group {chat_id}: {str(e)}")

    def remove_group(self, chat_id, bot_data):
        """Remove the group from the 'groups' table and update bot_data."""
        try:
            # Ensure chat_id is an integer
            chat_id = int(chat_id)  # Convert to integer if it's not already

            # Retrieve existing groups
            existing_groups = self.get_knowledge("groups")
            if isinstance(existing_groups, str):
                existing_groups = json.loads(existing_groups)  # Parse if it's a JSON string

            # Flatten the nested list if necessary
            if isinstance(existing_groups, list) and len(existing_groups) > 0:
                existing_groups = existing_groups[0]  # Get the first (and only) list

            # Log the type and content of existing_groups
            logger.info(f"Existing groups before removal: {existing_groups} (type: {type(existing_groups)})")
            logger.info(f"Attempting to remove group with chat_id: {chat_id} (type: {type(chat_id)})")

            # Ensure existing_groups is a list
            if not isinstance(existing_groups, list):
                logger.error(f"Expected a list of groups, but got {type(existing_groups)}")
                return

            # Find and remove the group
            updated_groups = [group for group in existing_groups if group['id'] != chat_id]
            if len(updated_groups) == len(existing_groups):
                logger.warning(f"Group {chat_id} not found in the knowledge base.")
                return

            # Update bot_data
            if 'group_members' in bot_data:
                bot_data['group_members'] = [group for group in bot_data['group_members'] if group['id'] != chat_id]

            # Store updated groups in the knowledge base
            self.store_knowledge("groups", json.dumps([updated_groups]))  # Wrap in a list again
            logger.info(f"Group {chat_id} removed from the knowledge base.")

        except Exception as e:
            logger.error(f"Error removing group {chat_id}: {str(e)}") 