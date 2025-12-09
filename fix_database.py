import os
import sys

def fix_database_check():
    """Fix all database checks in the code"""
    
    files_to_fix = [
        'website/app.py',
        'bot/bot.py'
    ]
    
    for filepath in files_to_fix:
        if os.path.exists(filepath):
            with open(filepath, 'r') as f:
                content = f.read()
            
            # Fix: if db: -> if db is not None:
            # Fix: if not db: -> if db is None:
            # Fix: if self.db: -> if self.db is not None:
            # Fix: if not self.db: -> if self.db is None:
            
            content = content.replace('if db:', 'if db is not None:')
            content = content.replace('if not db:', 'if db is None:')
            content = content.replace('if self.db:', 'if self.db is not None:')
            content = content.replace('if not self.db:', 'if self.db is None:')
            
            with open(filepath, 'w') as f:
                f.write(content)
            
            print(f"✅ Fixed {filepath}")
        else:
            print(f"❌ File not found: {filepath}")

if __name__ == "__main__":
    fix_database_check()
    print("\nRun this command to fix the database checks:")
    print("python fix_database.py")