import random
import datetime
import hashlib
import base64
from io import BytesIO

from flask import Flask, render_template, request, jsonify
import psycopg
from psycopg.rows import dict_row
import qrcode
from dotenv import load_dotenv
import os

load_dotenv()



app = Flask(__name__)

# ==========================================
# CONFIGURACIÓN DE BASE DE DATOS (SUPABASE)
# ==========================================

DATABASE_URL = os.getenv("DATABASE_URL")
print("DATABASE_URL cargada:", bool(DATABASE_URL))

def get_db_connection():
    return psycopg.connect(
        DATABASE_URL,
        sslmode="require",
        row_factory=dict_row
    )

# ==========================================
# VISTAS PRINCIPALES
# ==========================================

@app.route('/')
def home():
    return render_template('home.html')

@app.route('/cajero')
def cajero():
    return render_template('cajero.html')

# ==========================================
# BANCA WEB - PRODUCTOS
# ==========================================

@app.route('/banca-web/<int:id_usuario>')
def banca_web_productos(id_usuario):
    conn = get_db_connection()
    cur = conn.cursor()

    cur.execute("""
        SELECT c.*, 
               COALESCE(pn.nombre || ' ' || pn.apellido, pj.nombre_comercial) AS nombre_titular
        FROM CUENTA c
        LEFT JOIN PERSONA_NATURAL pn ON c.id_cliente = pn.id_persona
        LEFT JOIN PERSONA_JURIDICA pj ON c.id_cliente = pj.id_persona
        WHERE c.id_cliente = %s
    """, (id_usuario,))
    cuenta = cur.fetchone()

    if not cuenta:
        return "Usuario no encontrado", 404

    cur.execute("""
        SELECT *
        FROM TRANSACCIONES
        WHERE id_cuenta = %s
        ORDER BY fecha_transaccion DESC
        LIMIT 20
    """, (cuenta["id_cuenta"],))
    transacciones = cur.fetchall()

    cur.close()
    conn.close()

    return render_template(
        "banca_web.html",
        cuenta=cuenta,
        transacciones=transacciones,
        seccion="productos"
    )

# ==========================================
# BANCA WEB - RETIRO SIN TARJETA
# ==========================================

@app.route('/banca-web/<int:id_usuario>/retiro')
def banca_web_retiro(id_usuario):
    conn = get_db_connection()
    cur = conn.cursor()

    cur.execute("""
        SELECT c.*, 
               COALESCE(pn.nombre || ' ' || pn.apellido, pj.nombre_comercial) AS nombre_titular
        FROM CUENTA c
        LEFT JOIN PERSONA_NATURAL pn ON c.id_cliente = pn.id_persona
        LEFT JOIN PERSONA_JURIDICA pj ON c.id_cliente = pj.id_persona
        WHERE c.id_cliente = %s
    """, (id_usuario,))
    cuenta = cur.fetchone()

    cur.close()
    conn.close()

    return render_template(
        "banca_web.html",
        cuenta=cuenta,
        seccion="retiro"
    )

# ==========================================
# BANCA WEB - DEUNA (QR)
# ==========================================

@app.route('/banca-web/<int:id_usuario>/deuna')
def banca_web_deuna(id_usuario):
    conn = get_db_connection()
    cur = conn.cursor()

    cur.execute("""
        SELECT c.*, 
               COALESCE(pn.nombre || ' ' || pn.apellido, pj.nombre_comercial) AS nombre_titular,
               COALESCE(sd.saldo, 0) AS saldo_deuna
        FROM CUENTA c
        LEFT JOIN PERSONA_NATURAL pn ON c.id_cliente = pn.id_persona
        LEFT JOIN PERSONA_JURIDICA pj ON c.id_cliente = pj.id_persona
        LEFT JOIN SALDO_DEUNA sd ON c.id_cuenta = sd.id_cuenta
        WHERE c.id_cliente = %s
    """, (id_usuario,))
    cuenta = cur.fetchone()

    cur.close()
    conn.close()

    id_otro_usuario = 2 if id_usuario == 1 else 1
    link_qr = request.host_url + f"simulacion/escanear-qr/{id_otro_usuario}/{cuenta['id_cliente']}"

    # Generar QR (SIN format)
    qr = qrcode.QRCode(box_size=8, border=4)
    qr.add_data(link_qr)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")

    buffer = BytesIO()
    img.save(buffer)
    qr_b64 = base64.b64encode(buffer.getvalue()).decode()

    return render_template(
        "banca_web.html",
        cuenta=cuenta,
        seccion="deuna",
        qr_b64=qr_b64,
        id_otro_usuario=id_otro_usuario
    )

# ==========================================
# SIMULACIÓN ESCANEAR QR
# ==========================================

@app.route('/simulacion/escanear-qr/<int:id_pagador>/<int:id_cobrador>')
def simulacion_escanear_qr(id_pagador, id_cobrador):
    conn = get_db_connection()
    cur = conn.cursor()

    cur.execute("""
        SELECT * FROM CUENTA WHERE id_cliente = %s
    """, (id_pagador,))
    cuenta = cur.fetchone()

    cur.execute("""
        SELECT id_cuenta FROM CUENTA WHERE id_cliente = %s
    """, (id_cobrador,))
    destino = cur.fetchone()

    cur.close()
    conn.close()

    return render_template(
        "banca_web.html",
        cuenta=cuenta,
        seccion="deuna",
        modo_simulacion_pago=True,
        id_destino_prefill=destino["id_cuenta"]
    )

# ==========================================
# API - GENERAR ORDEN DE RETIRO
# ==========================================

@app.route('/api/generar-orden', methods=['POST'])
def generar_orden():
    data = request.json
    id_cuenta = data["id_cuenta"]
    monto = float(data["monto"])
    telefono = data["telefono"]

    otp = str(random.randint(100000, 999999))
    expiracion = datetime.datetime.now() + datetime.timedelta(minutes=30)

    conn = get_db_connection()
    cur = conn.cursor()

    cur.execute("SELECT saldo_actual FROM CUENTA WHERE id_cuenta = %s", (id_cuenta,))
    saldo = cur.fetchone()["saldo_actual"]

    if saldo < monto:
        return jsonify(success=False, message="Saldo insuficiente")

    cadena_base = f"{otp}-{id_cuenta}-{monto}-{datetime.datetime.now()}"
    hash_validacion = hashlib.sha256(cadena_base.encode()).hexdigest()

    conn = get_db_connection()
    cur = conn.cursor()

    cur.execute("SELECT saldo_actual FROM CUENTA WHERE id_cuenta = %s", (id_cuenta,))
    saldo = cur.fetchone()["saldo_actual"]

    if saldo < monto:
        return jsonify(success=False, message="Saldo insuficiente")

    # 2. AGREGAMOS 'hash_validacion' AL INSERT
    cur.execute("""
        INSERT INTO ORDEN_RETIRO
        (id_cuenta_origen, codigo_otp, monto, fecha_expiracion, telefono_destino, hash_validacion)
        VALUES (%s, %s, %s, %s, %s, %s)
    """, (id_cuenta, otp, monto, expiracion, telefono, hash_validacion)) 
    # ^^^ Nota que agregué hash_validacion al final de los valores

    conn.commit()
    cur.close()
    conn.close()

    return jsonify(success=True, otp=otp)

# ==========================================
# API - RECARGAR DEUNA
# ==========================================

@app.route('/api/recargar-saldo', methods=['POST'])
def recargar_saldo():
    data = request.json
    id_cuenta = data["id_cuenta"]
    monto = float(data["monto"])

    conn = get_db_connection()
    cur = conn.cursor()

    cur.execute("SELECT saldo_actual FROM CUENTA WHERE id_cuenta = %s", (id_cuenta,))
    saldo = cur.fetchone()["saldo_actual"]

    if saldo < monto:
        return jsonify(success=False, message="Saldo insuficiente")

    cur.execute("UPDATE CUENTA SET saldo_actual = saldo_actual - %s WHERE id_cuenta = %s", (monto, id_cuenta))
    cur.execute("""
        INSERT INTO SALDO_DEUNA (id_cuenta, saldo)
        VALUES (%s, %s)
        ON CONFLICT (id_cuenta)
        DO UPDATE SET saldo = SALDO_DEUNA.saldo + %s
    """, (id_cuenta, monto, monto))

    conn.commit()
    cur.close()
    conn.close()

    return jsonify(success=True)


# ==========================================
# API - EJECUTAR RETIRO (CAJERO)
# ==========================================

@app.route('/api/ejecutar-retiro', methods=['POST'])
def ejecutar_retiro():
    data = request.json
    otp = data.get("otp")
    telefono = data.get("telefono")

    conn = get_db_connection()
    cur = conn.cursor()

    # 1. Buscar la orden PENDIENTE que coincida con OTP y Teléfono
    cur.execute("""
        SELECT * FROM ORDEN_RETIRO 
        WHERE codigo_otp = %s 
          AND telefono_destino = %s 
          AND estado = 'PENDIENTE'
    """, (otp, telefono))
    
    orden = cur.fetchone()

    # Si no existe la orden o ya fue cobrada
    if not orden:
        cur.close()
        conn.close()
        return jsonify(success=False, message="Código incorrecto, expirado o ya utilizado.")

    # 2. Verificar si el código ha expirado (comparar fechas)
    if orden["fecha_expiracion"] < datetime.datetime.now():
        cur.close()
        conn.close()
        return jsonify(success=False, message="El código ha caducado.")

    try:
        # 3. Verificar saldo y CALCULAR EL NUEVO SALDO
        cur.execute("SELECT saldo_actual FROM CUENTA WHERE id_cuenta = %s", (orden["id_cuenta_origen"],))
        cuenta = cur.fetchone()
        
        if cuenta["saldo_actual"] < orden["monto"]:
            return jsonify(success=False, message="La cuenta origen no tiene fondos suficientes.")

        # Calculamos el nuevo saldo en una variable
        nuevo_saldo = cuenta["saldo_actual"] - orden["monto"]

        # 4. TRANSACCIÓN: Descontar dinero y cerrar orden
        
        # A) Actualizamos la CUENTA con el nuevo saldo calculado
        cur.execute("""
            UPDATE CUENTA 
            SET saldo_actual = %s 
            WHERE id_cuenta = %s
        """, (nuevo_saldo, orden["id_cuenta_origen"]))

        # B) Marcar la orden como COMPLETADO
        cur.execute("""
            UPDATE ORDEN_RETIRO 
            SET estado = 'COMPLETADO' 
            WHERE id_orden = %s
        """, (orden["id_orden"],))

        # C) Registrar en TRANSACCIONES (¡Ahora incluyendo saldo_resultante!)
        cur.execute("""
            INSERT INTO TRANSACCIONES (id_cuenta, tipo_transaccion, monto, descripcion, fecha_transaccion, saldo_resultante)
            VALUES (%s, 'DEBITO', %s, 'RETIRO CAJERO SIN TARJETA', NOW(), %s)
        """, (orden["id_cuenta_origen"], -orden["monto"], nuevo_saldo))

        conn.commit()
        success = True
        message = f"Retiro de ${orden['monto']} realizado con éxito."

    except Exception as e:
        # ... (el resto del código sigue igual)
        # 3. Verificar si la cuenta aún tiene saldo (doble chequeo de seguridad)
        cur.execute("SELECT saldo_actual FROM CUENTA WHERE id_cuenta = %s", (orden["id_cuenta_origen"],))
        cuenta = cur.fetchone()
        
        if cuenta["saldo_actual"] < orden["monto"]:
            return jsonify(success=False, message="La cuenta origen no tiene fondos suficientes.")

        # 4. TRANSACCIÓN: Descontar dinero y cerrar orden
        
        # A) Restar saldo a la cuenta
        cur.execute("""
            UPDATE CUENTA 
            SET saldo_actual = saldo_actual - %s 
            WHERE id_cuenta = %s
        """, (orden["monto"], orden["id_cuenta_origen"]))

        # B) Marcar la orden como COMPLETADO
        cur.execute("""
            UPDATE ORDEN_RETIRO 
            SET estado = 'COMPLETADO' 
            WHERE id_orden = %s
        """, (orden["id_orden"],))

        # C) Registrar en el historial de transacciones (Para que salga en Banca Web)
        cur.execute("""
            INSERT INTO TRANSACCIONES (id_cuenta, tipo_transaccion, monto, descripcion, fecha_transaccion)
            VALUES (%s, 'DEBITO', %s, 'RETIRO CAJERO SIN TARJETA', NOW())
        """, (orden["id_cuenta_origen"], -orden["monto"]))

        conn.commit()
        success = True
        message = f"Retiro de ${orden['monto']} realizado con éxito."

    except Exception as e:
        conn.rollback()
        success = False
        message = "Error interno en el servidor."
        print(f"Error transacción: {e}")

    finally:
        cur.close()
        conn.close()

    return jsonify(success=success, message=message)

# ==========================================
# MAIN
# ==========================================

if __name__ == "__main__":
    app.run(debug=True)
