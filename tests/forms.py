"""自動入力テスト用の、よくある形の応募フォーム。"""

# 表形式・姓名とフリガナが 2 分割・電話 3 分割・郵便 2 分割・都道府県セレクト・性別ラジオ
TABLE_FORM = """
<form><table>
<tr><th>お名前<span>必須</span></th><td><input name="n1" placeholder="姓"><input name="n2" placeholder="名"></td></tr>
<tr><th>フリガナ</th><td><input name="k1"><input name="k2"></td></tr>
<tr><th>メールアドレス</th><td><input name="mail"></td></tr>
<tr><th>メールアドレス（確認）</th><td><input name="mail2"></td></tr>
<tr><th>郵便番号</th><td><input name="z1" maxlength="3">-<input name="z2" maxlength="4"></td></tr>
<tr><th>都道府県</th><td><select name="pref"><option value="">選択</option><option value="13">東京都</option><option value="27">大阪府</option></select></td></tr>
<tr><th>市区町村</th><td><input name="addr_city"></td></tr>
<tr><th>番地</th><td><input name="addr_street"></td></tr>
<tr><th>建物名</th><td><input name="addr_bld"></td></tr>
<tr><th>電話番号</th><td><input name="t1" maxlength="4">-<input name="t2" maxlength="4">-<input name="t3" maxlength="4"></td></tr>
<tr><th>性別</th><td><label><input type="radio" name="sex" value="1">男性</label><label><input type="radio" name="sex" value="2">女性</label></td></tr>
<tr><th>生年月日</th><td><select name="by"><option></option><option>1989</option><option>1990</option></select>年
<select name="bm"><option></option><option>1</option><option>2</option></select>月
<select name="bd"><option></option><option>01</option><option>02</option></select>日</td></tr>
<tr><th>ご意見</th><td><textarea name="opinion"></textarea></td></tr>
<tr><th>規約</th><td><label><input type="checkbox" name="agree">応募規約に同意する</label></td></tr>
</table><button type="submit">送信</button></form>
"""

# label[for] 形式・autocomplete なし・ひらがな指定・ハイフンなし
LABEL_FORM = """
<form>
<label for="a">姓</label><input id="a" name="sei">
<label for="b">名</label><input id="b" name="mei">
<label for="c">せい（ひらがな）</label><input id="c" name="sei_kana" placeholder="やまだ">
<label for="d">めい（ひらがな）</label><input id="d" name="mei_kana" placeholder="たろう">
<label for="e">電話番号（ハイフンなし）</label><input id="e" name="tel">
<label for="f">郵便番号（ハイフンなし）</label><input id="f" name="zipcode">
<label for="g">ご住所</label><input id="g" name="address">
<label for="h">年齢</label><input id="h" name="age">
<label for="i">画像の文字を入力</label><input id="i" name="captcha">
<button>送信</button>
</form>
"""

# autocomplete 属性あり・React 風（value setter 経由でないと反映されない想定）
AUTOCOMPLETE_FORM = """
<form>
<input autocomplete="family-name" name="x1">
<input autocomplete="given-name" name="x2">
<input type="email" name="x3">
<input autocomplete="postal-code" name="x4">
<input autocomplete="address-level1" name="x5">
<input type="date" name="x6" autocomplete="bday">
<input name="x7" placeholder="ニックネーム">
</form>
"""
