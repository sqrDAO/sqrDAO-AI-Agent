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

    def _get_validated_groups(self) -> tuple[list, bool]:
        """Retrieve and validate existing groups from the knowledge base.
        
        Returns:
            tuple: (existing_groups, success_flag)
        """
        try:
            # Retrieve existing groups
            existing_groups = self.get_knowledge("groups")
            
            # Handle string/JSON parsing
            if isinstance(existing_groups, str):
                try:
                    existing_groups = json.loads(existing_groups)
                except json.JSONDecodeError:
                    logger.error("Failed to parse existing groups from knowledge base")
                    return None, False
            
            # Get the last element which contains the most recent group data
            if isinstance(existing_groups, list) and len(existing_groups) > 0:
                last_element = existing_groups[-1]
                if isinstance(last_element, list):
                    existing_groups = last_element
                else:
                    logger.error(f"Last element is not a list: {type(last_element)}")
                    return None, False
            else:
                logger.error("No groups data found or data is not in expected format")
                return None, False
            
            # Ensure existing_groups is a list
            if not isinstance(existing_groups, list):
                logger.error(f"Expected a list of groups, but got {type(existing_groups)}")
                return None, False
            
            return existing_groups, True
        except Exception as e:
            logger.error(f"Error retrieving groups: {str(e)}")
            return None, False

    def add_group(self, chat_id, groupname, bot_data) -> bool:
        """Add the group to the 'groups' table and update bot_data."""
        try:
            logger.debug(f"Adding group: chat_id={chat_id}, groupname={groupname}")
            
            # Input validation
            if not groupname or not str(groupname).strip():
                logger.error("Invalid groupname: cannot be empty or whitespace")
                return False
                
            if not isinstance(chat_id, (int, str)):
                logger.error(f"Invalid chat_id type: {type(chat_id)}")
                return False
                
            # Convert chat_id to integer if it's a string
            try:
                chat_id = int(chat_id)
            except (ValueError, TypeError):
                logger.error(f"Invalid chat_id format: {chat_id}")
                return False

            # Retrieve existing groups
            existing_groups, success = self._get_validated_groups()
            if not success:
                logger.error("Failed to validate existing groups.")
                return False

            # Check if the group already exists
            if any(group['id'] == chat_id for group in existing_groups):
                logger.warning(f"Group {chat_id} already exists in the knowledge base")
                return False

            # Add new group with the provided groupname
            new_group = {
                'id': chat_id,
                'title': str(groupname).strip(),
                'type': 'group',
                'added_at': datetime.now().isoformat()
            }
            existing_groups.append(new_group)

            # Update bot_data
            if 'group_members' not in bot_data:
                bot_data['group_members'] = []
            bot_data['group_members'].append(new_group)

            # Store updated groups in the knowledge base
            try:
                with self.conn:
                    self.store_knowledge("groups", json.dumps(existing_groups))
                    logger.debug(f"Successfully added group {chat_id} ({groupname}) to the knowledge base")
                    return True
            except Exception as e:
                logger.error(f"Failed to store updated groups in knowledge base: {str(e)}")
                return False

        except Exception as e:
            logger.error(f"Error adding group {chat_id}: {str(e)}")
            return False

    def remove_group(self, chat_id, bot_data) -> bool:
        """Remove the group from the 'groups' table and update bot_data."""
        try:
            # Ensure chat_id is an integer
            # Validate chat_id type first
            if not isinstance(chat_id, (int, str)):
                logger.error(f"Invalid chat_id type: {type(chat_id)}")
                return False
                
            # Convert chat_id to integer if it's a string
            try:
                chat_id = int(chat_id)
            except (ValueError, TypeError):
                logger.error(f"Invalid chat_id format: {chat_id}")
                return False

            # Retrieve existing groups
            existing_groups, success = self._get_validated_groups()
            if not success:
                return False

            # Log the type and content of existing_groups
            logger.debug(f"Existing groups before removal: {existing_groups} (type: {type(existing_groups)})")
            logger.debug(f"Attempting to remove group with chat_id: {chat_id} (type: {type(chat_id)})")

            # Find and remove the group
            updated_groups = [group for group in existing_groups if group['id'] != chat_id]
            logger.debug(f"Updated groups after removal attempt: {updated_groups}")

            if len(updated_groups) == len(existing_groups):
                logger.warning(f"Group {chat_id} not found in the knowledge base.")
                return False

            # Update bot_data
            if 'group_members' in bot_data:
                bot_data['group_members'] = [group for group in bot_data['group_members'] if group['id'] != chat_id]

            # Store updated groups in the knowledge base
            try:
                with self.conn:  # Uses connection as context manager
                    self.store_knowledge("groups", json.dumps(updated_groups))
                    logger.debug(f"Group {chat_id} removed from the knowledge base.")
                    return True
            except Exception as e:
                logger.error(f"Failed to store updated groups in knowledge base: {str(e)}")
                return False

        except Exception as e:
            logger.error(f"Error removing group {chat_id}: {str(e)}") 
            return False

    def load_members(self):
        """Load unique members from the database.
        
        Returns:
            list: A list of dictionaries, each containing 'username' and 'user_id' 
                 for each unique member. Returns an empty list if no data is found
                 or on error.
        """
        try:
            members_data = self.get_knowledge("members")
            unique_members = set()  # Use a set to avoid duplicates
            
            if not members_data or not isinstance(members_data, list):
                logger.warning("No members data found or data is not in expected format.")
                return []  # Return an empty list if no valid data

            for member_list in members_data:
                if not isinstance(member_list, list):
                    logger.warning(f"Expected a list of members, got {type(member_list)}")
                    continue
                for member in member_list:
                    if not isinstance(member, dict):
                        logger.warning(f"Expected a member dictionary, got {type(member)}")
                        continue
                    
                    # Add unique members based on username
                    if member.get('username') and member.get('user_id'):
                        unique_members.add((member['username'], member['user_id']))

            # Convert the set back to a list of dictionaries
            return [{'username': username, 'user_id': user_id} for username, user_id in unique_members]

        except Exception as e:
            logger.error(f"Error loading members: {str(e)}")
            return []  # Fallback to an empty list on error

    def load_groups(self):
        """Load groups from the database.
        
        Returns:
            list: A list of dictionaries containing group information with 'id', 'title',
                 'type', and 'added_at' fields. Returns an empty list if validation 
                 fails or on error.
        """
        try:
            # Reuse existing validation method
            existing_groups, success = self._get_validated_groups()
            if success:
                return existing_groups
            return []  # Return an empty list if validation failed

        except Exception as e:
            logger.error(f"Error loading groups: {str(e)}")
            return []  # Fallback to an empty list on error