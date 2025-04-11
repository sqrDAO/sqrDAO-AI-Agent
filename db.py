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