# Fact-ledger review — tom

## S01 (mood_range [6, 8])

- **F1** (hard, open, → checkin): Das lange Sonntagstelefonat war schön; Lea hat gut zugehört, als er vom Prüfstand erzählt hat.
  - Beleg: „Das lange Telefonat am Sonntag. Du hast gut zugehört, als ich vom Prüfstand erzählt habe.“
- **F2** (light, open, → main_work): Viel los in der Firma — mehr sagt er zunächst nicht dazu.
  - Beleg: „Viel los in der Firma. Sonst nichts.“
- **F3** (hard, if_probed, → main_work): Der Prüfstand muss Ende Januar zum Kunden; es kann eng werden mit den Wochenenden.
  - Beleg: „Der Prüfstand muss Ende Januar zum Kunden. Kann sein, dass es eng wird mit Wochenenden.“
- **F4** (light, if_asked, → main_work): Er braucht nichts Bestimmtes — nur ein bisschen Geduld, wenn er abends spät dran ist.
  - Beleg: „Nichts Bestimmtes. Bisschen Geduld, wenn ich abends spät dran bin.“
- **F5** (light, open, → closing): Er freut sich auf Leas Besuch und auf Fußball am Dienstag.
  - Beleg: „Auf deinen Besuch. Und Dienstag wieder Fußball.“
- **F6** (hard, open, → closing): Er übernimmt das Buchen der Zugtickets noch diese Woche.
  - Beleg: „Tickets buchen, ja. Mach ich diese Woche.“

## S02 (mood_range [6, 8])

- **H1** (light, if_asked, → checkin): Homework aus S01 war, eine kleine, unangekündigte Geste zu machen. Er hat vor Leas Besuch die Wohnung aufgeräumt und ihren Tee bereitgestellt, ohne dass sie danach gefragt hatte — für ihn selbstverständlich, kein großes Ding, deshalb erwähnt er es nicht von sich aus.
  - Beleg: „Du hattest die ganze Wohnung aufgeräumt und mein Lieblingstee stand schon da, ohne dass ich was gesagt hätte.“
  - Hinweis: Cross-referenced from Lea's own W02 script (partner_a), not Tom's own — this bot's factledger validator allows homework facts (id prefix H) to verify against either patient's own script within the vendored week, since Tom characteristically shows care through action rather than narrating it himself (see testdata/patients/tom/patient.json's intervention_response_policy).
- **F1** (light, open, → checkin): Der Donnerstag-Telefonabend hat stattgefunden und gepasst.
  - Beleg: „Donnerstag telefoniert. Hat gepasst.“
- **F2** (hard, open, → checkin): Das Wochenende war schön; er würdigt, dass Lea den weiten Weg schon wieder auf sich genommen hat.
  - Beleg: „Das Wochenende. Und dass du den weiten Weg schon wieder auf dich genommen hast.“
- **F3** (hard, open, → main_work): Die Abgabe beim Prüfstand rückt näher; sonst passt es.
  - Beleg: „Abgabe beim Prüfstand rückt näher. Sonst passt es.“
- **F4** (hard, if_probed, → main_work): Falls die Abnahme in Győr vorgezogen wird, könnte der Januar chaotisch werden — noch nicht sicher.
  - Beleg: „Falls die Abnahme in Győr vorgezogen wird, könnte der Januar noch chaotisch werden. Ist aber noch nicht sicher.“
- **F5** (light, if_asked, → main_work): Er braucht ruhige Abende, mehr nicht.
  - Beleg: „Ruhige Abende. Mehr nicht.“
- **F6** (hard, open, → closing): Er freut sich auf Hamburg am 23. und hat schon nach Zügen geschaut.
  - Beleg: „Hamburg am 23. Habe schon nach Zügen geschaut.“
- **F7** (hard, open, → closing): Termine beim Abschied festmachen ist für ihn ok; mittags losfahren am 23. versucht er — hängt an der Firma.
  - Beleg: „Termine beim Abschied festmachen ist ok. Mittags losfahren am 23. versuche ich, hängt an der Firma.“

## S03 (mood_range [4, 7])

- **H1** (light, if_asked, → checkin): Homework aus S02 war, auch mal explizit zu sagen (nicht nur zu zeigen), wenn ihm etwas fehlt oder wichtig ist. Ansatzweise geschafft: Dienstag nach dem Fußball hat er, todmüde, trotzdem noch eine Sprachnachricht geschickt statt es einfach zu lassen — für ihn schon eine Ansage, auch wenn er es selbst nicht so nennen würde.
  - Beleg: „Dass du mir Dienstag nach dem Fußball noch eine Sprachnachricht geschickt hast, obwohl du todmüde warst – die habe ich mir zweimal angehört.“
  - Hinweis: Cross-referenced from Lea's own W03 script (partner_a) — Tom's own script doesn't narrate the gesture himself, consistent with his intervention_response_policy.
- **F1** (light, open, → checkin): Der Besuchstermin steht: 23. Januar — für ihn ist der Fokus damit erledigt.
  - Beleg: „Termin steht, 23. Januar. Insofern: erledigt.“
- **F2** (light, open, → checkin): Fußball am Dienstag: 7:3 gewonnen.
  - Beleg: „Fußball am Dienstag, wir haben 7:3 gewonnen.“
- **F3** (light, open, → checkin): Leas Paket mit den Espressobohnen ist angekommen — er ist dankbar.
  - Beleg: „Und dein Paket mit den Espressobohnen kam an – danke.“
- **F4** (hard, open, → main_work): Stress mit der Abnahme: Sailer redet jetzt doch davon, dass Győr nächste Woche stattfindet.
  - Beleg: „Stress mit der Abnahme. Sailer redet jetzt doch von Győr nächste Woche.“
- **F5** (hard, if_probed, → main_work): Schlimmster Fall: Flug am Donnerstag und das Wochenende in Győr — sicher weiß er es erst Mittwoch.
  - Beleg: „Heißt im schlimmsten Fall: Ich fliege Donnerstag und bin das Wochenende dort. Ich weiß es aber erst Mittwoch sicher.“
- **F6** (hard, if_probed, → main_work): Auf die Bedürfnis-Frage sagt er erst 'Nichts.' — auf Nachfrage: dass niemand sauer ist, wenn die Firma ihm dazwischenfunkt, denn das kann er nicht steuern.
  - Beleg: „Na gut: dass niemand sauer ist, wenn die Firma mir dazwischenfunkt. Das kann ich nämlich nicht steuern.“
- **F7** (light, if_asked, → closing): Er freut sich auf Hamburg — wenn es klappt.
  - Beleg: „Hamburg, wenn es klappt.“
- **F8** (hard, open, → closing): Er sagt Lea bis Mittwochabend Bescheid, ob der 23. steht.
  - Beleg: „Ich sage dir bis Mittwochabend Bescheid wegen dem 23.“

## S04 (mood_range [3, 6])

- **H1** (hard, if_probed, → checkin, main_work): Homework aus S03 war, bei kurzfristigen Planänderungen anzurufen statt nur zu schreiben. Nicht geschafft — als sich der Besuch kurzfristig änderte, hat er wieder nur eine Nachricht geschrieben. Im Nachhinein sagt er, das hätte er anders machen können, aber in dem Moment war er nur am Organisieren und wollte es schnell hinter sich bringen.
  - Beleg: „Der Anruf-Punkt kommt an, das hätte ich anders machen können. Aber in dem Moment war ich nur am Organisieren und wollte es schnell hinter mich bringen.“
- **F1** (hard, open, → main_work): Aus seiner Sicht: das Wochenende hat nicht geklappt, die Firma ging vor — konnte er nicht ändern.
  - Beleg: „Hat nicht geklappt. Firma ging vor, konnte ich nicht ändern.“
- **F2** (hard, if_asked, → checkin): Schön war wenig; immerhin ist die Abnahme in Győr durch — das war wichtig.
  - Beleg: „Wenig. Die Abnahme in Győr ist durch, das war wichtig.“
- **F3** (hard, open, → main_work): Seine Rahmung der Absage: keine Wahl — Sailer hat Mittwoch entschieden, Donnerstag stand der Flug; dass Lea so sauer ist, findet er hart, er hat sich das Wochenende nicht ausgesucht.
  - Beleg: „Ich hatte keine Wahl, Sailer hat Mittwoch entschieden, Donnerstag stand der Flug. Dass du so sauer bist, finde ich hart – ich habe mir das Wochenende nicht ausgesucht.“
- **F4** (hard, if_probed, → main_work): Sein Zugeständnis (kommt erst auf Nachfrage): der Anruf-Punkt kommt an, das hätte er anders machen können — in dem Moment war er nur am Organisieren und wollte es schnell hinter sich bringen.
  - Beleg: „Der Anruf-Punkt kommt an, das hätte ich anders machen können. Aber in dem Moment war ich nur am Organisieren und wollte es schnell hinter mich bringen.“
- **F5** (hard, open, → main_work): Sein Bedürfnis: die Sache nicht größer machen als sie ist — 'Es war ein Arbeitswochenende, kein Statement.'
  - Beleg: „Dass wir das nicht größer machen als es ist. Es war ein Arbeitswochenende, kein Statement.“
- **F6** (hard, refuse, → main_work): Ob ihm das Wochenende selbst gefehlt hat, spricht er nicht aus — genau das, was Lea hören will. Auf Drängen weicht er in Sachlichkeit aus ('Arbeitswochenende, kein Statement') statt Gefühle zu benennen.
  - Beleg: „Es war ein Arbeitswochenende, kein Statement.“
- **F7** (light, open, → closing): Er freut sich auf einen normalen Besuch ohne Firmentheater.
  - Beleg: „Auf einen normalen Besuch ohne Firmentheater.“
- **F8** (hard, open, → closing): Er schaut heute Abend in den Dienstplan und schickt Lea zwei Terminoptionen für einen Ersatzbesuch; die Anruf-Regel ist für ihn ok.
  - Beleg: „Ich schaue heute Abend in den Dienstplan und schicke dir zwei Optionen. Die Anruf-Regel ist ok.“
