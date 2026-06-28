import socket
import asyncio
import random
from flask import Flask, render_template, request
from flask_socketio import SocketIO, emit
from zeroconf import ServiceInfo, Zeroconf
from network_manager import NetworkManager

app = Flask(__name__)
app.config['SECRET_KEY'] = 'bridge-secret'
socketio = SocketIO(app, cors_allowed_origins="*")

SUITS = ['Spades', 'Hearts', 'Diamonds', 'Clubs']
# סדר חשיבות הקלפים (משמאל לימין) - המשחק ישתמש באינדקס לחישוב הזוכה
RANKS = ['2', '3', '4', '5', '6', '7', '8', '9', '10', 'J', 'Q', 'K', 'A']
TRUMP_SUIT = 'Spades'

game_state = {
    "players": [], # בדיוק 4 שחקנים
    "sids": {}, # מיפוי בין שם לשחקן ל-Socket ID כדי לשלוח קלפים אישיים
    "hands": {}, # הקלפים של כל שחקן
    "bids": {}, # ההימורים (הכרזות) של כל שחקן
    "tricks_won": {}, # לקיחות שכל שחקן ניצח בפועל
    "phase": "waiting", # waiting, bidding, playing, finished
    "turn_index": 0, # תור נוכחי במערך השחקנים
    "current_trick": [], # הקלפים ששוחקו בסיבוב הנוכחי (מערך של מילונים)
    "led_suit": None, # הסדרה שפתחה את הסיבוב הנוכחי
    "scores": {} # ניקוד סופי
}

def generate_deck():
    deck = [{'suit': suit, 'rank': rank} for suit in SUITS for rank in RANKS]
    random.shuffle(deck)
    return deck

def get_local_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(('10.255.255.255', 1))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        pass
    try:
        hostname = socket.gethostname()
        ips = socket.gethostbyname_ex(hostname)[2]
        for ip in ips:
            if ip.startswith('192.168.137.'):
                return ip
        for ip in ips:
            if not ip.startswith('127.'):
                return ip
    except Exception:
        pass
    return '127.0.0.1'

def setup_mdns(ip, port):
    zc = Zeroconf()
    info = ServiceInfo(
        "_http._tcp.local.",
        "Bridge._http._tcp.local.",
        addresses=[socket.inet_aton(ip)],
        port=port,
        server="play.local."
    )
    zc.register_service(info)
    return zc

def evaluate_trick():
    """חישוב המנצח בסיבוב הנוכחי (לקיחה)"""
    winning_card = None
    winner_name = None
    
    for play in game_state["current_trick"]:
        card = play['card']
        player = play['player']
        
        if winning_card is None:
            winning_card = card
            winner_name = player
            continue
            
        # האם נזרק שליט כשהמנצח הנוכחי אינו שליט?
        if card['suit'] == TRUMP_SUIT and winning_card['suit'] != TRUMP_SUIT:
            winning_card = card
            winner_name = player
        # האם נזרק קלף מאותה סדרה מנצחת אבל גבוה יותר?
        elif card['suit'] == winning_card['suit']:
            if RANKS.index(card['rank']) > RANKS.index(winning_card['rank']):
                winning_card = card
                winner_name = player
                
    return winner_name

def calculate_scores():
    """חישוב הניקוד: עמידה בהימור = 10 נק' + הימור. אי עמידה = נקודה לכל לקיחה (או מינוס)."""
    for player in game_state["players"]:
        bid = game_state["bids"].get(player, 0)
        won = game_state["tricks_won"].get(player, 0)
        
        if bid == won:
            game_state["scores"][player] = 10 + (bid * 2)
        else:
            game_state["scores"][player] = won - abs(bid - won)

def broadcast_state():
    """שידור המצב הכללי לכולם"""
    state_to_send = {
        "players": game_state["players"],
        "phase": game_state["phase"],
        "bids": game_state["bids"],
        "tricks_won": game_state["tricks_won"],
        "current_trick": game_state["current_trick"],
        "led_suit": game_state["led_suit"],
        "scores": game_state["scores"]
    }
    if len(game_state["players"]) > 0:
        state_to_send["current_turn_player"] = game_state["players"][game_state["turn_index"]]
    else:
        state_to_send["current_turn_player"] = None
        
    emit('game_update', state_to_send, broadcast=True)

@app.route('/')
def index():
    return render_template('index.html')

@socketio.on('join_game')
def handle_join(data):
    player_name = data.get('name')
    if len(game_state["players"]) < 4 and player_name not in game_state["players"]:
        game_state["players"].append(player_name)
    
    # עדכון או שמירה של ה-SID לשחקן זה
    if player_name in game_state["players"]:
        game_state["sids"][player_name] = request.sid
        
    broadcast_state()
    
    # שליחת היד מחדש לשחקן במידה והתנתק וחזר
    if player_name in game_state["hands"]:
        emit('private_hand', {'hand': game_state["hands"][player_name]}, to=request.sid)

@socketio.on('start_game')
def handle_start():
    if len(game_state["players"]) == 4:
        deck = generate_deck()
        game_state["phase"] = "bidding"
        game_state["turn_index"] = 0
        game_state["bids"] = {}
        game_state["tricks_won"] = {p: 0 for p in game_state["players"]}
        game_state["current_trick"] = []
        game_state["scores"] = {}
        
        for i, player in enumerate(game_state["players"]):
            hand = deck[i*13 : (i+1)*13]
            hand.sort(key=lambda c: (SUITS.index(c['suit']), RANKS.index(c['rank'])), reverse=True)
            game_state["hands"][player] = hand
            
            sid = game_state["sids"].get(player)
            if sid:
                emit('private_hand', {'hand': hand}, to=sid)
                
        broadcast_state()

@socketio.on('place_bid')
def handle_bid(data):
    player_name = data.get('player')
    bid = int(data.get('bid'))
    
    if game_state["phase"] == "bidding" and game_state["players"][game_state["turn_index"]] == player_name:
        game_state["bids"][player_name] = bid
        game_state["turn_index"] = (game_state["turn_index"] + 1) % 4
        
        # אם כולם הימרו, עוברים למשחק
        if len(game_state["bids"]) == 4:
            game_state["phase"] = "playing"
            game_state["turn_index"] = 0 # הראשון משחק
            
        broadcast_state()

@socketio.on('play_card')
def handle_play_card(data):
    player_name = data.get('player')
    card = data.get('card')
    
    if game_state["phase"] != "playing":
        return
        
    # וידוא שזה אכן תורו
    if game_state["players"][game_state["turn_index"]] != player_name:
        return
        
    hand = game_state["hands"][player_name]
    
    # חיפוש הקלף ביד השחקן
    played_card = next((c for c in hand if c['suit'] == card['suit'] and c['rank'] == card['rank']), None)
    if not played_card:
        return
        
    # אכיפת חוקי משחק (חובת שירות)
    if len(game_state["current_trick"]) == 0:
        game_state["led_suit"] = played_card['suit']
    else:
        led = game_state["led_suit"]
        has_led_suit = any(c['suit'] == led for c in hand)
        if has_led_suit and played_card['suit'] != led:
            emit('error_message', {'msg': f'חובה לשחק {led} כי יש לך ביד!'}, to=request.sid)
            return

    # הוצאה מהיד והוספה לשולחן
    hand.remove(played_card)
    game_state["current_trick"].append({'player': player_name, 'card': played_card})
    
    emit('private_hand', {'hand': hand}, to=request.sid)
    
    # אם 4 קלפים שוחקו - סיום הלקיחה
    if len(game_state["current_trick"]) == 4:
        winner = evaluate_trick()
        game_state["tricks_won"][winner] += 1
        
        # המנצח פותח בסיבוב הבא
        game_state["turn_index"] = game_state["players"].index(winner)
        broadcast_state() # שידור הלוח המלא לפני הניקוי
        
        socketio.sleep(2.5) # השהייה קצרה כדי שכולם יראו מי ניצח בלקיחה
        
        game_state["current_trick"] = []
        game_state["led_suit"] = None
        
        # בדיקת סיום המשחק (אין קלפים ביד של אף אחד)
        if len(game_state["hands"][player_name]) == 0:
            game_state["phase"] = "finished"
            calculate_scores()
            
        broadcast_state()
    else:
        # העברת התור לשחקן הבא
        game_state["turn_index"] = (game_state["turn_index"] + 1) % 4
        broadcast_state()

def start_server():
    net = NetworkManager()
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(net.initialize_network())
    
    port = 5000
    ip = get_local_ip()
    zc = setup_mdns(ip, port)
    
    print(f"\n[!] השרת פועל! התחבר לנקודה החמה ופתח בדפדפן: http://play.local:{port}")
    try:
        socketio.run(app, host='0.0.0.0', port=port)
    finally:
        zc.unregister_all_services()
        zc.close()

if __name__ == '__main__':
    start_server()