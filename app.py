from flask import Flask, render_template, request, redirect, session
import sqlite3, os, qrcode, random

app = Flask(__name__)
app.secret_key = "supermarket_secret"
DB_FILE = "database.db"

# =====================================================
# DATABASE & MIGRATION
# =====================================================

def get_db():
    return sqlite3.connect(DB_FILE)

def column_exists(cur, table, column):
    cur.execute(f"PRAGMA table_info({table})")
    return column in [c[1] for c in cur.fetchall()]

def migrate_db():
    db = get_db()
    cur = db.cursor()

    # USERS
    cur.execute("""
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        email TEXT UNIQUE,
        password TEXT,
        role TEXT
    )
    """)

    # PRODUCTS
    cur.execute("""
    CREATE TABLE IF NOT EXISTS products (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT,
        price INTEGER,
        quantity INTEGER,
        shelf TEXT,
        bin TEXT,
        category TEXT
    )
    """)

    # ORDERS (BASE)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS orders (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        product_id INTEGER,
        status TEXT
    )
    """)

    # AUTO MIGRATION COLUMNS
    migrations = {
        "orders": {
            "qr": "TEXT",
            "otp": "TEXT",
            "delivery_name": "TEXT",
            "delivery_phone": "TEXT"
        }
    }

    for table, cols in migrations.items():
        for col, dtype in cols.items():
            if not column_exists(cur, table, col):
                cur.execute(f"ALTER TABLE {table} ADD COLUMN {col} {dtype}")

    # SEED USERS
    users = [
        ("admin","admin","admin"),
        ("user","user","user"),
        ("picker","picker","picker"),
        ("packer","packer","packer"),
        ("delivery","delivery","delivery")
    ]

    for u in users:
        cur.execute("SELECT 1 FROM users WHERE email=?", (u[0],))
        if not cur.fetchone():
            cur.execute("INSERT INTO users VALUES (NULL,?,?,?)", u)

    # SEED PRODUCTS
    cur.execute("SELECT COUNT(*) FROM products")
    if cur.fetchone()[0] == 0:
        categories = ["Fruits","Dairy","Atta","Meat","Masala","Packaged","Cleaning"]
        for i in range(1, 51):
            cur.execute("""
            INSERT INTO products VALUES (NULL,?,?,?,?,?,?)
            """, (
                f"Product {i}",
                40 + i,
                50,
                "A",
                f"Bin-{i%5+1}",
                categories[i % len(categories)]
            ))

    db.commit()
    db.close()

migrate_db()

# =====================================================
# AUTH
# =====================================================

@app.route("/", methods=["GET","POST"])
def login():
    if request.method == "POST":
        db = get_db()
        cur = db.cursor()
        cur.execute(
            "SELECT role FROM users WHERE email=? AND password=?",
            (request.form["email"], request.form["password"])
        )
        user = cur.fetchone()
        if not user:
            return "Invalid login"

        session["role"] = user[0]
        return redirect(f"/{user[0]}")

    return render_template("login.html")

@app.route("/logout")
def logout():
    session.clear()
    return redirect("/")

# =====================================================
# USER
# =====================================================

@app.route("/user")
def user():
    db = get_db()
    cur = db.cursor()
    cur.execute("SELECT * FROM products")
    return render_template("products.html", items=cur.fetchall())

@app.route("/order/<int:pid>")
def order(pid):
    otp = str(random.randint(10000,99999))
    db = get_db()
    cur = db.cursor()
    cur.execute(
        "INSERT INTO orders (product_id,status,otp) VALUES (?,?,?)",
        (pid, "Pending", otp)
    )
    db.commit()
    return redirect("/cart")

@app.route("/cart")
def cart():
    db = get_db()
    cur = db.cursor()
    cur.execute("""
    SELECT orders.id, products.name, orders.status, orders.otp
    FROM orders JOIN products ON orders.product_id = products.id
    """)
    return render_template("cart.html", data=cur.fetchall())

# =====================================================
# PICKER
# =====================================================

@app.route("/picker")
def picker():
    db = get_db()
    cur = db.cursor()
    cur.execute("""
    SELECT orders.id, products.name, products.shelf, products.bin
    FROM orders JOIN products ON orders.product_id = products.id
    WHERE orders.status='Pending'
    """)
    return render_template("picker.html", orders=cur.fetchall())

# MAIN PICK ROUTE
@app.route("/pick/<int:oid>")
def pick(oid):
    db = get_db()
    cur = db.cursor()
    cur.execute("UPDATE orders SET status='Picked' WHERE id=?", (oid,))
    db.commit()
    return redirect("/picker")

# BACKWARD COMPATIBILITY (FIXES YOUR 404)
@app.route("/mark_picked/<int:oid>")
def mark_picked(oid):
    return redirect(f"/pick/{oid}")

# =====================================================
# PACKER (QR GENERATION)
# =====================================================

@app.route("/packer")
def packer():
    db = get_db()
    cur = db.cursor()
    cur.execute("""
    SELECT orders.id, products.name
    FROM orders JOIN products ON orders.product_id = products.id
    WHERE orders.status='Picked'
    """)
    return render_template("packer.html", orders=cur.fetchall())

@app.route("/pack/<int:oid>")
def pack(oid):
    os.makedirs("static/qr", exist_ok=True)
    qr_path = f"qr/order_{oid}.png"
    qrcode.make(f"ORDER:{oid}").save(f"static/{qr_path}")

    db = get_db()
    cur = db.cursor()
    cur.execute(
        "UPDATE orders SET status='Packed', qr=? WHERE id=?",
        (qr_path, oid)
    )
    db.commit()
    return redirect("/packer")

# =====================================================
# DELIVERY (MULTI STEP)
# =====================================================

@app.route("/delivery")
def delivery():
    db = get_db()
    cur = db.cursor()
    cur.execute("""
    SELECT 
        orders.id, 
        products.name, 
        orders.qr, 
        orders.status, 
        orders.delivery_name
    FROM orders
    JOIN products ON orders.product_id = products.id
    WHERE orders.status IN ('Packed','Delivered')
    """)
    return render_template("delivery.html", orders=cur.fetchall())


@app.route("/verify_qr/<int:oid>", methods=["POST"])
def verify_qr(oid):
    file = request.files.get("qrfile")
    if not file:
        return "No QR uploaded"

    # (Demo verification — real scanning optional)
    # If file exists, we assume QR is valid
    return redirect(f"/delivery_details/{oid}")


@app.route("/delivery_details/<int:oid>", methods=["GET","POST"])
def delivery_details(oid):
    if request.method == "POST":
        db = get_db()
        cur = db.cursor()
        cur.execute("""
        UPDATE orders SET delivery_name=?, delivery_phone=?
        WHERE id=?
        """, (
            request.form["name"],
            request.form["phone"],
            oid
        ))
        db.commit()
        return redirect(f"/verify_otp/{oid}")

    return render_template("delivery_details.html", oid=oid)


@app.route("/verify_otp/<int:oid>", methods=["GET","POST"])
def verify_otp(oid):
    if request.method == "POST":
        db = get_db()
        cur = db.cursor()
        cur.execute("SELECT otp FROM orders WHERE id=?", (oid,))
        real = cur.fetchone()[0]

        if request.form["otp"] == real:
            cur.execute(
                "UPDATE orders SET status='Delivered' WHERE id=?",
                (oid,)
            )
            db.commit()
            return redirect("/delivery")

        return "OTP mismatch"

    return render_template("verify_otp.html", oid=oid)

# =====================================================
# ADMIN
# =====================================================

@app.route("/admin")
def admin():
    return render_template("admin.html")

@app.route("/admin/reset_db")
def reset_db():
    if session.get("role") != "admin":
        return "Unauthorized"

    if os.path.exists(DB_FILE):
        os.remove(DB_FILE)

    migrate_db()
    return redirect("/admin")

# =====================================================
# RUN
# =====================================================

app.run(debug=True)
