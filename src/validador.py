def validar_saldos(transacciones, tolerancia=0.01):
    """
    Valida que la cadena de saldos sea aritméticamente consistente.
    
    Para cada transacción i, verifica:
        saldo[i] == saldo[i-1] + deposito[i] - retiro[i]
    
    Modifica las transacciones en su lugar agregando dos campos:
        - valido: True / False / None (None para la primera, no validable)
        - error_saldo: float, diferencia entre saldo esperado y observado
    
    Args:
        transacciones: lista de dicts (output de parsear_transacciones)
        tolerancia: máxima diferencia aceptable en valor absoluto
    
    Returns:
        la misma lista (modificada in-place) y un dict con resumen.
    """
    if not transacciones:
        return transacciones, {"total": 0, "validas": 0, "invalidas": 0, "no_validables": 0}
    
    # Primera transacción: no validable (no hay saldo anterior)
    transacciones[0]["valido"] = None
    transacciones[0]["error_saldo"] = None
    
    validas = 0
    invalidas = 0
    
    for i in range(1, len(transacciones)):
        prev = transacciones[i - 1]
        curr = transacciones[i]
        
        # Si el saldo es None (no se extrajo), no podemos validar
        if curr["saldo"] is None or prev["saldo"] is None:
            curr["valido"] = None
            curr["error_saldo"] = None
            continue
        
        saldo_esperado = prev["saldo"] + curr["deposito"] - curr["retiro"]
        error = curr["saldo"] - saldo_esperado
        
        curr["error_saldo"] = round(error, 4)
        curr["valido"] = abs(error) < tolerancia
        
        if curr["valido"]:
            validas += 1
        else:
            invalidas += 1
    
    resumen = {
        "total": len(transacciones),
        "validas": validas,
        "invalidas": invalidas,
        "no_validables": len(transacciones) - validas - invalidas,
    }
    
    return transacciones, resumen
